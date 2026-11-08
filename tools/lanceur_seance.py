#!/usr/bin/env python3
"""Lanceur de séance du PC 2 : petite fenêtre toujours au premier plan.

Coche chaque étape (gris : en attente, jaune : en cours, vert : lancé et vérifié, rouge : échec) :
  1. serveur joignable (GET /sante) ;
  2. OBS lancé et obs-websocket qui accepte le mot de passe ;
  3. pont OBS (obs/obs_quiz_bridge.py) connecté à OBS et au serveur ;
  4. télécommande ouverte et connectée (rôle `remote` vu par /sante).

Puis des voyants mis à jour toutes les 2 s : Serveur, Scène (projecteur du PC 1, rôle `scene`), OBS (scène courante,
enregistrement), Pont. Le pont est un processus enfant sans fenêtre : il est relancé s'il tombe, et arrêté
quand on ferme la fenêtre. Son journal est copié dans data/pont-obs.log.

Secrets lus dans lancer-seance.local.bat (hors git) : set "OBS_MDP=…" et set "AMPHI_JETON=…".

    .venv\\Scripts\\pythonw.exe tools\\lanceur_seance.py      (ou double-clic sur lancer-seance.bat)
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import messagebox

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "obs"))
from obs_quiz_bridge import Obs, connexion_ouverte  # noqa: E402

SERVEUR = "https://q.phare360.com"
OBS_EXE = Path(r"C:\Program Files\obs-studio\bin\64bit\obs64.exe")
OBS_HOTE, OBS_PORT = "127.0.0.1", 4455
FICHIER_SECRETS = RACINE / "lancer-seance.local.bat"
JOURNAL = RACINE / "data" / "pont-obs.log"
SANS_FENETRE = getattr(subprocess, "CREATE_NO_WINDOW", 0)

GRIS, JAUNE, VERT, ROUGE = "#9aa0a6", "#f2b600", "#22a447", "#d93025"
FOND, TEXTE, DISCRET = "#1f2329", "#f1f3f4", "#9aa0a6"


def lire_secrets(fichier: Path) -> dict[str, str]:
    """Lit les lignes `set "NOM=valeur"` (ou `set NOM=valeur`) d'un .bat."""
    valeurs: dict[str, str] = {}
    if not fichier.exists():
        return valeurs
    for ligne in fichier.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r'^\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=(.*?)"?\s*$', ligne, re.IGNORECASE)
        if m:
            valeurs[m.group(1).upper()] = m.group(2)
    return valeurs


def http_json(url: str, delai: float = 4) -> dict:
    requete = urllib.request.Request(url, headers={"User-Agent": "amphi-replay-lanceur", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(requete, timeout=delai) as r:
        return json.loads(r.read().decode("utf-8"))


def port_ouvert(hote: str, port: int, delai: float = 0.5) -> bool:
    try:
        with socket.create_connection((hote, port), timeout=delai):
            return True
    except OSError:
        return False


def obs_lance() -> bool:
    try:
        sortie = subprocess.run(["tasklist", "/fi", "imagename eq obs64.exe", "/fo", "csv", "/nh"],
                                capture_output=True, text=True, creationflags=SANS_FENETRE, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "obs64.exe" in sortie.lower()


PORT_POSTPROD = 8000


async def arreter_enregistrement(mdp: str) -> bool | None:
    """StopRecord si OBS enregistre ; attend la fermeture du fichier. True = arrêté, False = rien à arrêter, None = échec."""
    obs = Obs(f"ws://{OBS_HOTE}:{OBS_PORT}", mdp)
    try:
        await obs.connecter()
        if not await obs.enregistrement_actif():
            return False
        await obs.requete("StopRecord")
        for _ in range(40):  # le fichier est finalisé quand OBS ne signale plus d'enregistrement actif
            await asyncio.sleep(0.25)
            if not await obs.enregistrement_actif():
                return True
        return True
    except Exception:  # noqa: BLE001
        return None
    finally:
        if obs.ws is not None:
            await obs.ws.close()


def ouvrir_postprod(serveur: str, jeton: str, prez: str) -> None:
    """Post-production locale (deploy/postprod.ps1) préremplie : serveur de séance, son jeton (par l'environnement, jamais
    dans l'URL), présentation active, séances chargées d'office. Déjà lancée → la page est simplement rouverte."""
    url = f"http://127.0.0.1:{PORT_POSTPROD}/deck/postprod.html?token=local" + (f"&prez={prez}" if prez else "") + "&auto=1"
    if port_ouvert("127.0.0.1", PORT_POSTPROD):
        webbrowser.open(url)
        return
    env = {**os.environ, "AMPHI_SEANCE_JETON": jeton, "AMPHI_JETON": "local"}
    commande = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(RACINE / "deploy" / "postprod.ps1"),
                "--seance", serveur, "--auto"] + (["--prez", prez] if prez else [])
    # Console visible : le journal du serveur local y défile ; la fermer arrête la post-production.
    subprocess.Popen(commande, cwd=RACINE, env=env, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))


def arreter_ponts_orphelins() -> None:
    """Arrête un pont resté d'un lancement précédent (sinon deux ponts piloteraient OBS)."""
    commande = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*.exe' -and "
                f"$_.CommandLine -like '*obs_quiz_bridge*' -and $_.ProcessId -ne {os.getpid()} }} | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    subprocess.run(["powershell", "-NoProfile", "-Command", commande], creationflags=SANS_FENETRE,
                   capture_output=True, timeout=30)


class Lanceur:
    ETAPES = [("serveur", "Serveur joignable"), ("obs", "OBS lancé (WebSocket vérifié)"),
              ("pont", "Pont OBS connecté"), ("remote", "Télécommande connectée")]
    VOYANTS = [("serveur", "Serveur"), ("scene", "Scène"), ("obs", "OBS"), ("pont", "Pont")]

    def __init__(self) -> None:
        secrets = lire_secrets(FICHIER_SECRETS)
        self.mdp = secrets.get("OBS_MDP", "")
        self.jeton = secrets.get("AMPHI_JETON", "")
        self.serveur = secrets.get("SERVEUR", SERVEUR).rstrip("/")
        self.evenements: queue.Queue = queue.Queue()
        self.pont: subprocess.Popen | None = None
        self.pont_obs = self.pont_serveur = False
        self.arret = threading.Event()
        self.remote_ouverte = False

        try:  # texte net sur un écran mis à l'échelle (125 %, 150 %…)
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
        self.fen = tk.Tk()
        self.fen.title("Séance amphi-replay")
        self.fen.configure(bg=FOND)
        self.fen.attributes("-topmost", True)
        self.fen.resizable(False, False)
        self.fen.protocol("WM_DELETE_WINDOW", self.quitter)
        self.echelle = max(1.0, self.fen.winfo_fpixels("1i") / 96)  # tailles en pixels × mise à l'échelle Windows
        self._construire()
        self.fen.update_idletasks()
        largeur = self.fen.winfo_width()
        self.fen.geometry(f"+{self.fen.winfo_screenwidth() - largeur - 24}+24")

    # ------------------------------------------------------------------ interface

    def px(self, valeur: float) -> int:
        return int(round(valeur * self.echelle))

    def _pastille(self, parent, taille=14) -> tuple[tk.Canvas, int]:
        taille = self.px(taille)
        c = tk.Canvas(parent, width=taille, height=taille, bg=FOND, highlightthickness=0)
        rond = c.create_oval(1, 1, taille - 1, taille - 1, fill=GRIS, outline="")
        return c, rond

    def _construire(self) -> None:
        police, gras, petite = ("Segoe UI", 10), ("Segoe UI Semibold", 10), ("Segoe UI", 8)
        cadre = tk.Frame(self.fen, bg=FOND, padx=14, pady=10)
        cadre.pack(fill="both")
        tk.Label(cadre, text="Lancement de la séance", font=("Segoe UI Semibold", 11), fg=TEXTE, bg=FOND).pack(anchor="w")

        self.etapes: dict[str, tuple[tk.Canvas, int, tk.Label]] = {}
        bloc = tk.Frame(cadre, bg=FOND, pady=6)
        bloc.pack(fill="x")
        for i, (cle, titre) in enumerate(self.ETAPES):
            c, rond = self._pastille(bloc)
            c.grid(row=i * 2, column=0, padx=(0, 8), pady=(4, 0))
            tk.Label(bloc, text=titre, font=police, fg=TEXTE, bg=FOND, anchor="w").grid(row=i * 2, column=1, sticky="w", pady=(4, 0))
            detail = tk.Label(bloc, text="en attente", font=petite, fg=DISCRET, bg=FOND, anchor="w", wraplength=self.px(300), justify="left")
            detail.grid(row=i * 2 + 1, column=1, sticky="w")
            self.etapes[cle] = (c, rond, detail)

        tk.Frame(cadre, bg="#3c4043", height=1).pack(fill="x", pady=6)
        ligne = tk.Frame(cadre, bg=FOND)
        ligne.pack(fill="x")
        self.voyants: dict[str, tuple[tk.Canvas, int, tk.Label]] = {}
        for i, (cle, titre) in enumerate(self.VOYANTS):
            bloc = tk.Frame(ligne, bg=FOND)
            bloc.grid(row=0, column=i, padx=(0, self.px(6)), sticky="n")
            c, rond = self._pastille(bloc, 18)
            c.pack()
            tk.Label(bloc, text=titre, font=gras, fg=TEXTE, bg=FOND).pack()
            info = tk.Label(bloc, text="—", font=petite, fg=DISCRET, bg=FOND, width=14, wraplength=self.px(100))
            info.pack()
            self.voyants[cle] = (c, rond, info)

        boutons = tk.Frame(cadre, bg=FOND, pady=8)
        boutons.pack(fill="x")
        self.bouton_fini = tk.Button(boutons, text="✔ Fini", command=self.fini, font=("Segoe UI Semibold", 9), bg=VERT, fg="#ffffff",
                                     relief="flat", activebackground="#1b8a3a", activeforeground="#ffffff", padx=8)
        self.bouton_fini.pack(side="left", padx=(0, 10))
        for texte, action in (("Télécommande", self.ouvrir_remote), ("Journal", self.ouvrir_journal),
                              ("Relancer", self.relancer), ("Arrêter", self.quitter)):
            tk.Button(boutons, text=texte, command=action, font=petite, bg="#3c4043", fg=TEXTE, relief="flat",
                      activebackground="#5f6368", activeforeground=TEXTE, padx=6).pack(side="left", padx=(0, 6))

    def etape(self, cle: str, couleur: str, texte: str) -> None:
        self.evenements.put(("etape", cle, couleur, texte))

    def voyant(self, cle: str, couleur: str, texte: str) -> None:
        self.evenements.put(("voyant", cle, couleur, texte))

    def _depiler(self) -> None:
        try:
            while True:
                genre, cle, couleur, texte = self.evenements.get_nowait()
                if genre == "bouton":
                    self.bouton_fini.configure(text=texte, state="normal")
                    continue
                c, rond, label = (self.etapes if genre == "etape" else self.voyants)[cle]
                c.itemconfigure(rond, fill=couleur)
                label.configure(text=texte, fg=ROUGE if couleur == ROUGE else DISCRET)
        except queue.Empty:
            pass
        if not self.arret.is_set():
            self.fen.after(150, self._depiler)

    # ------------------------------------------------------------------ actions

    def ouvrir_remote(self) -> None:
        self.remote_ouverte = True
        webbrowser.open(f"{self.serveur}/remote" + (f"?token={self.jeton}" if self.jeton else ""))

    # ------------------------------------------------------------------ fin de séance

    def fini(self) -> None:
        """Fin de séance : arrête l'enregistrement OBS s'il tourne, puis ouvre la post-production locale préremplie."""
        if not messagebox.askyesno("Séance terminée ?", "Terminer la séance ?\n\n• l'enregistrement OBS est arrêté s'il tourne encore ;\n"
                                   "• la post-production s'ouvre sur ce PC, avec la présentation, la vidéo et la séance déjà choisies.\n\n"
                                   "Le pont et la télécommande restent ouverts.", parent=self.fen):
            return
        self.bouton_fini.configure(state="disabled", text="… fin de séance")
        threading.Thread(target=self._fini, daemon=True).start()

    def _fini(self) -> None:
        try:
            if self.mdp and port_ouvert(OBS_HOTE, OBS_PORT):
                arrete = asyncio.run(arreter_enregistrement(self.mdp))
                self.voyant("obs", VERT if arrete is not None else ROUGE, "enregistrement arrêté" if arrete else ("rien à arrêter" if arrete is False else "arrêt impossible"))
            prez = ""
            try:
                prez = http_json(self.serveur + "/api/presentations/active", 4).get("slug") or ""
            except Exception:  # noqa: BLE001
                pass
            ouvrir_postprod(self.serveur, self.jeton, prez)
            self.evenements.put(("bouton", "", "", "✔ Post-production ouverte"))
        except Exception as e:  # noqa: BLE001
            self.evenements.put(("bouton", "", "", "✔ Fini"))
            self.fen.after(0, lambda: messagebox.showerror("Post-production", f"Impossible d'ouvrir la post-production : {e}", parent=self.fen))

    def ouvrir_journal(self) -> None:
        if JOURNAL.exists():
            os.startfile(JOURNAL)  # type: ignore[attr-defined]

    def relancer(self) -> None:
        self.arreter_pont()
        for cle, _ in self.ETAPES:
            self.etape(cle, GRIS, "en attente")
        threading.Thread(target=self.sequence, daemon=True).start()

    def quitter(self) -> None:
        if self.pont and self.pont.poll() is None and not messagebox.askyesno(
                "Arrêter la séance ?", "Fermer cette fenêtre arrête le pont OBS (bascules de scène et enregistrement "
                "pilotés par les diapositives).\n\nOBS et la télécommande restent ouverts.\n\nArrêter ?", parent=self.fen):
            return
        self.arret.set()
        self.arreter_pont()
        self.fen.destroy()

    # ------------------------------------------------------------------ pont

    def demarrer_pont(self) -> None:
        python = Path(sys.executable).with_name("python.exe")
        JOURNAL.parent.mkdir(exist_ok=True)
        self.pont_obs = self.pont_serveur = False
        self.pont = subprocess.Popen(
            [str(python if python.exists() else sys.executable), "-u", str(RACINE / "obs" / "obs_quiz_bridge.py"),
             self.serveur, "--obs-mdp", self.mdp] + (["--jeton", self.jeton] if self.jeton else []),
            cwd=RACINE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, creationflags=SANS_FENETRE)
        threading.Thread(target=self._lire_pont, args=(self.pont,), daemon=True).start()

    def _lire_pont(self, processus: subprocess.Popen) -> None:
        with open(JOURNAL, "a", encoding="utf-8") as journal:
            journal.write(f"\n--- pont lancé {time.strftime('%d/%m/%Y %H:%M:%S')} ---\n")
            for ligne in processus.stdout:  # type: ignore[union-attr]
                journal.write(ligne)
                journal.flush()
                ligne = ligne.strip()
                if ligne.startswith("OBS connecté"):
                    self.pont_obs = True
                elif ligne.startswith("serveur connecté"):
                    self.pont_serveur = True
                elif ligne.startswith("connexion perdue"):
                    self.pont_serveur = False
                    if "(OBS)" in ligne:
                        self.pont_obs = False
                    self.dernier_message_pont = ligne

    def arreter_pont(self) -> None:
        if self.pont and self.pont.poll() is None:
            # taskkill /T : le python.exe du venv lance un second processus, il faut arrêter l'arbre entier
            subprocess.run(["taskkill", "/PID", str(self.pont.pid), "/T", "/F"], capture_output=True, creationflags=SANS_FENETRE)
        self.pont = None

    # ------------------------------------------------------------------ séquence de lancement

    def attendre(self, condition, secondes: float) -> bool:
        fin = time.time() + secondes
        while time.time() < fin and not self.arret.is_set():
            if condition():
                return True
            time.sleep(0.5)
        return False

    def sequence(self) -> None:
        # 1. serveur
        self.etape("serveur", JAUNE, self.serveur)
        try:
            sante = http_json(self.serveur + "/sante")
            self.etape("serveur", VERT, f"OK · présentation « {sante.get('presentation', '?')} »")
        except Exception as e:  # noqa: BLE001
            self.etape("serveur", ROUGE, f"injoignable : {e} (la captation OBS reste possible, F9)")

        # 2. OBS
        if not self.mdp:
            self.etape("obs", ROUGE, f"mot de passe absent de {FICHIER_SECRETS.name} (set \"OBS_MDP=…\")")
            return
        if obs_lance():
            self.etape("obs", JAUNE, "déjà ouvert · vérification du WebSocket…")
        elif OBS_EXE.exists():
            self.etape("obs", JAUNE, "démarrage d'OBS…")
            subprocess.Popen([str(OBS_EXE)], cwd=OBS_EXE.parent)  # cwd obligatoire, sinon OBS ne trouve pas ses fichiers
        else:
            self.etape("obs", ROUGE, f"introuvable : {OBS_EXE}")
            return
        if not self.attendre(lambda: port_ouvert(OBS_HOTE, OBS_PORT), 60):
            self.etape("obs", ROUGE, "WebSocket fermé : activer Outils → Paramètres du serveur WebSocket (port 4455)")
        else:
            erreur = self.verifier_obs()
            if erreur:
                self.etape("obs", ROUGE, erreur)
            else:
                self.etape("obs", VERT, "OK · mot de passe accepté")

        # 3. pont
        self.etape("pont", JAUNE, "démarrage…")
        arreter_ponts_orphelins()
        self.demarrer_pont()
        if self.attendre(lambda: self.pont_obs and self.pont_serveur, 45):
            self.etape("pont", VERT, "OK · connecté à OBS et au serveur")
        else:
            self.etape("pont", ROUGE, getattr(self, "dernier_message_pont", "pas de connexion (voir Journal)")[:160])

        # 4. télécommande
        if not self.jeton:
            self.etape("remote", ROUGE, f"jeton absent de {FICHIER_SECRETS.name} (set \"AMPHI_JETON=…\")")
            return
        self.etape("remote", JAUNE, "ouverture dans le navigateur…")
        if not self.remote_ouverte:
            self.ouvrir_remote()
        if self.attendre(lambda: self.compter_role("remote") > 0, 60):
            self.etape("remote", VERT, "OK · connectée au serveur")
        else:
            self.etape("remote", ROUGE, "pas vue par le serveur (onglet fermé ? jeton refusé ?)")

    def verifier_obs(self) -> str:
        """Identification complète auprès d'obs-websocket ; renvoie le message d'erreur, ou '' si tout va bien."""
        async def essai() -> None:
            client = Obs(f"ws://{OBS_HOTE}:{OBS_PORT}", self.mdp)
            try:
                await client.connecter()
            finally:
                if client.ws is not None:
                    await client.ws.close()
        for _ in range(10):  # OBS ouvre le port un peu avant d'être prêt
            try:
                asyncio.run(essai())
                return ""
            except RuntimeError as e:
                if "refusée" in str(e):
                    return "mot de passe obs-websocket refusé (lancer-seance.local.bat)"
                derniere = str(e)
            except Exception as e:  # noqa: BLE001
                derniere = str(e)
            time.sleep(1)
        return f"WebSocket OBS : {derniere}"

    def compter_role(self, role: str) -> int:
        try:
            return int(http_json(self.serveur + "/sante", 3).get("deck", {}).get(role, 0))
        except Exception:  # noqa: BLE001
            return 0

    # ------------------------------------------------------------------ voyants

    def surveiller(self) -> None:
        threading.Thread(target=lambda: asyncio.run(self._surveiller()), daemon=True).start()

    async def _surveiller(self) -> None:
        obs = Obs(f"ws://{OBS_HOTE}:{OBS_PORT}", self.mdp)
        while not self.arret.is_set():
            # Serveur et projecteur
            try:
                sante = await asyncio.to_thread(http_json, self.serveur + "/sante", 3)
                etat = await asyncio.to_thread(http_json, self.serveur + "/etat", 3)
                self.voyant("serveur", VERT, "en ligne")
                n = int(sante.get("deck", {}).get("scene", 0))
                diapo = f"diapo {int(etat.get('h', 0)) + 1}"
                self.voyant("scene", VERT if n else ROUGE, f"projecteur · {diapo}" if n else "projecteur absent")
            except Exception:  # noqa: BLE001
                self.voyant("serveur", ROUGE, "injoignable")
                self.voyant("scene", GRIS, "inconnu")

            # OBS : notre propre connexion obs-websocket (le pont n'expose pas son état)
            if not self.mdp:
                self.voyant("obs", ROUGE, "mot de passe ?")
            elif not port_ouvert(OBS_HOTE, OBS_PORT):
                obs.ws = None
                self.voyant("obs", ROUGE, "fermé" if not obs_lance() else "WebSocket fermé")
            else:
                try:
                    if not connexion_ouverte(obs.ws):
                        await obs.connecter()
                    scene = await obs.scene_courante()
                    rec = await obs.enregistrement_actif()
                    self.voyant("obs", VERT, f"{scene}" + (" · ● REC" if rec else ""))
                except Exception as e:  # noqa: BLE001
                    obs.ws = None
                    self.voyant("obs", ROUGE, "mot de passe refusé" if "refusée" in str(e) else "ne répond pas")

            # Pont (processus enfant) : relancé automatiquement s'il tombe
            if self.pont is None:
                self.voyant("pont", GRIS, "arrêté")
            elif self.pont.poll() is not None:
                self.voyant("pont", ROUGE, "tombé · relance")
                self.etape("pont", JAUNE, "le pont s'est arrêté, relance…")
                self.demarrer_pont()
            elif self.pont_obs and self.pont_serveur:
                self.voyant("pont", VERT, "connecté")
            else:
                self.voyant("pont", JAUNE, "reconnexion…")
            await asyncio.sleep(2)

    def lancer(self) -> None:
        threading.Thread(target=self.sequence, daemon=True).start()
        self.surveiller()
        self._depiler()
        self.fen.mainloop()


PORT_INSTANCE = 47231  # verrou : une seule fenêtre (deux ponts piloteraient OBS en même temps)


def verrou_instance() -> socket.socket | None:
    """Réserve un port local ; None si une autre fenêtre de lancement l'a déjà."""
    verrou = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        verrou.bind(("127.0.0.1", PORT_INSTANCE))
    except OSError:
        verrou.close()
        return None
    return verrou


if __name__ == "__main__":
    _verrou = verrou_instance()
    if _verrou is None:
        racine = tk.Tk()
        racine.withdraw()
        racine.attributes("-topmost", True)
        messagebox.showinfo("Séance amphi-replay", "La fenêtre de lancement est déjà ouverte "
                            "(regarder en haut à droite de l'écran ou dans la barre des tâches).", parent=racine)
        sys.exit(0)
    Lanceur().lancer()
