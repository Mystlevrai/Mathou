# verif-bot — vérification par QR code

```
scan du QR code ──> discord.gg/xxxx  (invitation permanente vers #verification)
                      │
                      ├─ nouveau sur le serveur ─> il rejoint ─────────────> vérif auto
                      └─ déjà membre ───────────> arrive sur #verification ─> clique « Vérifier mon compte »
                                                                               │
                                  compte dans liste.txt ? ── oui ──> ✅ message en MP
                                                          └─ non ──> 🚨 alerte dans #logs
```

Le bot tourne **sur ton PC** : il se connecte à Discord en sortie, **aucun port à
ouvrir**, pas de serveur web. Bot Discord **séparé** de mathou / anime-bot.

> ⚠️ Le PC doit être allumé avec le bot lancé. Bot coupé = personne n'est vérifié
> (les arrivées pendant ce temps ne sont pas rattrapées, le bouton affiche
> « Échec de l'interaction »).

---

## 1. Créer le bot Discord (une fois)

1. https://discord.com/developers/applications → **New Application** (ex. `Verif`).
2. Onglet **Bot** :
   - **Reset Token** → copie le jeton (c'est `DISCORD_TOKEN`, ne le partage jamais).
   - **Privileged Gateway Intents** → active **SERVER MEMBERS INTENT**
     (nécessaire pour la vérif auto à l'arrivée ; sinon mets `VERIFY_ON_JOIN=false`).
3. Onglet **OAuth2 → URL Generator** :
   - Scopes : `bot` + `applications.commands`
   - Permissions : **Voir les salons**, **Envoyer des messages**, **Intégrer des liens**,
     **Créer une invitation**
   - Ouvre l'URL générée → ajoute le bot à ton serveur.

## 2. Préparer le serveur

- Salon **#verification** : visible par tout le monde (lecture seule suffit, on
  clique juste le bouton). C'est là qu'arrive la personne qui scanne.
- Salon **#logs** : réservé aux admins, le bot doit pouvoir y écrire.
- Discord → Paramètres utilisateur → **Avancés → Mode développeur** : ON.
  Clic droit sur le serveur / un salon / un rôle → **Copier l'identifiant**.

## 3. Installer sur le PC (Windows)

1. Installe **Python 3.10+** : https://www.python.org/downloads/ — coche
   **« Add python.exe to PATH »**.
2. Récupère le code (`git pull` dans ton dossier mathou, ou `git clone https://github.com/Mystlevrai/Mathou.git`).
3. Double-clic sur **`verif-bot\start.bat`** :
   - 1er lancement : il crée `.env` et l'ouvre dans le Bloc-notes. Remplis au minimum
     `DISCORD_TOKEN`, `GUILD_ID`, `LOG_CHANNEL_ID`, enregistre, ferme.
4. Ouvre **`verif-bot\liste.txt`** (créé au 1er lancement) et mets les comptes autorisés,
   **un ID Discord par ligne** (voir les exemples dedans). Pas besoin de redémarrer
   le bot quand tu modifies la liste.
5. Relance **`start.bat`**. La fenêtre doit afficher `Connecte comme ...`.
   Laisse-la ouverte (tu peux la réduire).

Les 2 avertissements `PyNaCl ... / davey is not installed, voice will NOT be supported`
sont normaux (pas de vocal).

## 4. Dans Discord (une fois)

Dans **#verification** :

| Commande | Effet |
|---|---|
| `/panneau` | poste le message avec le bouton **Vérifier mon compte** |
| `/qrcode` | crée l'invitation permanente vers ce salon et te renvoie le **QR code** (PNG) à imprimer |

Les deux acceptent `salon:` pour viser un autre salon. Réservées à ceux qui ont
« Gérer le serveur ».

## 5. Tester

1. Mets **ton** ID dans `liste.txt` → clique le bouton → tu reçois le MP.
2. Retire-le (ou mets `#` devant) → clique le bouton (après 60 s) → alerte dans #logs.

## Lancer le bot au démarrage de Windows

`Win + R` → `shell:startup` → dans le dossier qui s'ouvre : clic droit → **Nouveau →
Raccourci** → cible : le chemin complet de `start.bat`. Dans les propriétés du
raccourci : **Exécuter : Réduite**. Le bot démarre à chaque ouverture de session.

`start.bat` relance le bot tout seul s'il plante ou si internet coupe. Il ne
s'arrête que sur une erreur de config (message affiché, appuie sur une touche).

## Réglages (`.env`)

| Clé | Rôle |
|---|---|
| `DISCORD_TOKEN` | jeton du bot |
| `GUILD_ID` | ID du serveur (commandes dispo tout de suite) |
| `LOG_CHANNEL_ID` | salon des alertes (**obligatoire**) |
| `ALERT_ROLE_ID` | rôle mentionné dans chaque alerte (vide = pas de mention) |
| `VERIFY_ON_JOIN` | `true` = vérif auto à l'arrivée sur le serveur ; `false` = bouton seulement |
| `LIST_FILE` | fichier de la liste (défaut `liste.txt`) |
| `DM_MESSAGE` | texte du MP ; `\n` = retour à la ligne ; variables `{mention}` `{name}` `{server}` |
| `CHECK_COOLDOWN` | secondes mini entre 2 vérifs d'un même compte (anti-spam d'alertes) |

## Ce qui part dans #logs

- 🚨 **Compte non reconnu** (rouge, + mention de `ALERT_ROLE_ID`) : mention, pseudo, ID,
  date de création du compte, déclencheur (arrivée / bouton).
- ⚠️ **Compte reconnu — MP impossible** (orange) : la personne est dans la liste mais a
  fermé ses MP ; le bot lui dit comment les ouvrir.

## Dépannage

- **`DISCORD_TOKEN invalide`** : recopie le jeton (Reset Token) dans `.env`.
- **`Active 'SERVER MEMBERS INTENT'`** : étape 1.2, ou `VERIFY_ON_JOIN=false`.
- **`/panneau` / `/qrcode` n'apparaissent pas** : `GUILD_ID` vide → attendre ~1 h, ou le remplir.
- **Rien dans #logs** : mauvais `LOG_CHANNEL_ID`, ou le bot n'a pas accès au salon.
- **Quelqu'un dans la liste reçoit une alerte** : vérifie l'ID (mode développeur →
  Copier l'identifiant **de l'utilisateur**). Un nom d'utilisateur marche aussi, mais
  s'il le change il n'est plus reconnu.
