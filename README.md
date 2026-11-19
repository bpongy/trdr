# trdr

Robot de trading sur ETH/USDT, pour un compte virtuel Binance (testnet ou demo trading).
Le code ne sait pas passer d'ordre sur un compte réel.

## Fonctionnement

- **Stratégie** : sur des bougies de 4 h, le robot achète les replis dans une tendance haussière.
  La tendance est donnée par les moyennes mobiles 50 et 200, l'entrée par le RSI qui repasse au-dessus de 40.
- **Sorties** : chaque position a un stop-loss (−5 %), un take-profit (+10 %) et une durée maximale (7 jours).
- **Taille** : montant fixe par trade (20 USDT) et nombre limité de positions ouvertes en même temps (1).
- **Deux modes**, à changer depuis l'API :
  - `manual` (par défaut) : le robot propose un signal, l'achat attend ta validation ;
  - `auto` : le robot achète seul.
- **Telegram** ne sert qu'aux notifications (signal, achat, vente, erreur).

Toutes ces valeurs se règlent dans le `.env` (voir `.env.example`).

## Structure

```
backend/
  trdr/
    strategy.py, indicators.py   stratégie et indicateurs
    backtest.py                  backtest sur historique
    bot.py                       boucle du robot (seul processus qui parle à Binance)
    exchange.py                  client Binance, verrouillé sur les comptes virtuels
    notifier.py                  notifications Telegram
    api/                         API FastAPI et authentification
    models.py, db.py, config.py  tables, base, configuration
  tests/
deploy/nginx/                    exemple de configuration Nginx
docker-compose.yml               db (PostgreSQL), api, bot
front/                           interface web, à venir
```

Le robot et l'API partagent le même code Python et la même base : l'API marque les signaux validés
et les positions à clôturer, le robot les traite à son passage suivant (toutes les 60 secondes).
L'API ne reçoit donc jamais les clés Binance.

## Démarrage en local

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Créer le `.env` à partir de `.env.example` (dans `backend/` en local, à la racine pour Docker), puis :

```bash
python -m trdr.cli init-auth                    # mot de passe + clé Google Authenticator
uvicorn --factory trdr.api.main:create_app      # API sur http://127.0.0.1:8000, doc sur /api/docs
python -m trdr.bot                              # robot (clés du compte virtuel requises)
```

En local, la base est un fichier SQLite (`trdr.db`).

## Backtest

```bash
python -m trdr.backtest --days 365          # télécharge l'historique public Binance
python -m trdr.backtest --csv bougies.csv   # ou un fichier time,open,high,low,close,volume
```

Le backtest utilise les paramètres du `.env`, compte 0,1 % de frais par ordre et achète à l'ouverture
de la bougie qui suit le signal.

## API

Connexion : `POST /api/auth/login` avec `{"password": "...", "code": "123456"}` renvoie un jeton à passer
ensuite dans l'en-tête `Authorization: Bearer <jeton>`.

| Méthode | Chemin | Rôle |
| --- | --- | --- |
| GET | `/api/health` | état du service (sans authentification) |
| GET | `/api/status` | mode, positions ouvertes, signaux en attente |
| PUT | `/api/settings/mode` | passer en `manual` ou `auto` |
| GET | `/api/signals` | derniers signaux |
| POST | `/api/signals/{id}/approve` | valider un signal |
| POST | `/api/signals/{id}/reject` | ignorer un signal |
| GET | `/api/positions` | positions (`?status=open` ou `closed`) |
| POST | `/api/positions/{id}/close` | demander la clôture d'une position |

## Déploiement (Docker)

```bash
cp .env.example .env    # compléter, y compris POSTGRES_PASSWORD
docker compose run --rm api python -m trdr.cli init-auth    # puis coller les trois lignes dans .env
docker compose up -d --build
```

L'API écoute sur `127.0.0.1:8000`. Le HTTPS est assuré par le Nginx du serveur :
voir `deploy/nginx/trdr.conf.example`.

## Sécurité

- Connexion par mot de passe et code Google Authenticator ; un code ne sert qu'une fois,
  et cinq échecs bloquent la connexion pendant 15 minutes.
- Sans les trois variables d'authentification, l'API refuse toutes les connexions.
- Créer les clés Binance sur le compte virtuel uniquement ; elles restent dans le `.env`, jamais dans le dépôt.

## Limites connues

- Le stop-loss et le take-profit sont surveillés par le robot (toutes les 60 secondes), pas déposés chez
  Binance : si le robot est arrêté, les positions ne sont plus protégées. Un ordre OCO réglerait ce point.
- Si le robot est coupé pendant un achat, le signal reste à l'état `executing` et une alerte est envoyée
  au redémarrage : il faut alors vérifier le compte à la main.
- Les tables sont créées au démarrage, sans outil de migration (Alembic à prévoir avant de faire évoluer le schéma).
- L'historique du testnet peut être trop court ou peu réaliste pour une moyenne à 200 bougies ;
  `MARKET_DATA_SOURCE=live` fait alors calculer les signaux sur les cours réels.
