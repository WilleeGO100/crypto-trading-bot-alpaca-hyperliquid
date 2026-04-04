# Linux VM Transition Layout

This repo is now structured so runtime code stays unchanged while Linux deployment files live under `deploy/linux/`.

## Folder order (recommended)

1. `src/` and root `*.py` runtime modules: trading engine and feeders.
2. `config/`: static strategy configs.
3. `data/`: runtime state and outputs (`LiveFeed.csv`, selector/scanner JSON, engine state).
4. `deploy/linux/`: Linux VM launch + service files.

## What to run on VM

From repo root:

```bash
chmod +x deploy/linux/run_alpaca.sh deploy/linux/run_hyperliquid.sh
cp deploy/linux/.env.vm.example .env
# fill keys in .env
./deploy/linux/run_alpaca.sh
```

Or Hyperliquid:

```bash
./deploy/linux/run_hyperliquid.sh
```

## Systemd setup

Copy one service file and enable it:

```bash
sudo cp deploy/linux/systemd/alpaca-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now alpaca-bot
sudo systemctl status alpaca-bot
```

Use `hyperliquid-bot.service` similarly for Hyperliquid mode.
