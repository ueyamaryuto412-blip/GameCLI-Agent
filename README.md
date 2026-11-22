# GameCLI Agent

## 日本語の初見ゲーム実況

このフォークでは、ダッシュボードの初期設定を「Codex CLI (ChatGPT login)」と「初見実況」にしています。Codex CLI で `codex login` を実行して ChatGPT でサインインし、`codex login status` で確認してください。プレイ中の画面を画像として Codex に送信し、契約の利用枠を使います。

Windows で下記の依存関係を用意して `run.bat` を起動し、[ダッシュボード](http://localhost:5173) を開きます。Target Type で Window、Target Name でゲームのウィンドウを選びます。Game Instructions に今回の目的と操作説明を入れて Start を押してください。モデルは「Codex CLI default」のまま使えます。

ダッシュボードの初見実況では、その回の観察をもとに一手と日本語の台詞を生成し、時刻付きの結果を `backend/Narration/` に保存します。音声付きの動画を作るには、以下の収録コマンドを使います。

### Slay the Spire 2 のローカル収録

収録時の声は「VOICEVOX:ずんだもん」のノーマルです。初回はセットアップで音声モデル、辞書、CPU用ランタイムを取得します。STS2 のソロのランを開きます。
リポジトリ直下で次を実行すると、現在のゲーム画面を読み、日本語で実況して操作します。
試作は6分で打ち切り、ゲームは開いたままにします。進行はゲーム側で自動保存されます。

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\backend\setup_zundamon.ps1
Set-Location backend
..\.venv\Scripts\python.exe sts2_pilot.py --minutes 6
```

`--observe` を付けると、今見えている場面の実況と録音・録画だけを試せます。
収録結果は `recordings/sts2-日時/pilot.mp4` です。STS2のウィンドウとゲーム音に
ずんだもんの声と字幕を重ね、次の判断を待つ区間をカットします。末尾に音声クレジットを表示します。Windowsの日本語デスクトップ音声を使う場合は `--voice windows` を指定します。元映像と台詞・時刻も
同じフォルダーに残ります。YouTubeに公開する前に、このMP4を人間がレビューできます。

ライブ収録では、画面を見たプレイヤーが「どれを選ぶか」と「その場で言いたい一言」を同じ応答で決めます。台詞があれば実際に読み上げ、声が終わってからゲームに入力します。選択の結果は次の画面で確かめます。動画にはその場で鳴らした音声を使います。元の判断、予定操作、送信を試みた入力、次に見えた結果は `narration.json` に、音声は `spoken/` に保存されます。

カード報酬だけを収録するなど目的を変えたいときは `--objective "カード報酬を選んで、結果を確認したら終了"` を渡してください。候補自体はゲームを見てから選びます。店への寄り道、カード選び、ピンチでの守り方に関する好みは `Game Instructions.slaythespire2.md` で編集できます。

### 収録済みの映像から台詞を編集する

STS2の実況者は「買い物好きで、心配になると暗算を始める」性格です。店に行きたい理由、被弾の予想、決着後の財布といった、その回を通して追える話を組み立てます。プレイ中の人物設定は `Game Instructions.slaythespire2.md`、スライム戦の編集台本は `episodes/slime-shop.json` にあります。

リポジトリ直下で次を実行すると、ゲームを起動せずに再編集できます。台本の `start` と `end` は元映像の秒数、`text` は台詞と字幕です。空の台詞はゲーム音だけのカットです。必要に応じて `offset` で発声を遅らせ、`reading` で読みを指定できます。

```powershell
.\.venv\Scripts\python.exe backend/sts2_pilot.py --edit recordings/sts2-20261122-210231-498855 --script episodes/slime-shop.json
```

ずんだもん音声の公開条件は[音源利用ガイドライン](https://zunko.jp/con_ongen_kiyaku.html)に従います。

An autonomous AI agent that plays turn-based games by watching your screen and controlling your mouse and keyboard. It uses large language models (LLMs) with vision capabilities to analyze game screenshots, make strategic decisions, and execute actions — all in real time.

> **Windows only** — relies on Windows APIs for input simulation (`pydirectinput`, `ctypes` SendInput).

## Features

- **Vision-based gameplay** — captures screenshots, draws coordinate rulers for precision, and sends them to an LLM for analysis
- **Multiple LLM providers** — Claude Code CLI (free, uses your Claude login), Antigravity CLI (`agy`, free), Gemini CLI (free), Gemini API, or OpenRouter (access to GPT-4o, etc.)
- **Smart action verification** — OpenCV template matching + OCR to correct coordinate drift before clicking
- **Retry & recovery** — automatic retries with coordinate nudging, mid-retry LLM consultation, and loop detection
- **Game phase detection** — OCR-based detection of combat, map, card rewards, rest sites, shops, and events
- **Tiered memory** — turn-level, combat-level, and run-level memory so the agent learns from its mistakes
- **Turn boundary enforcement** — ensures the agent ends its turn when it should
- **Post-combat reflection** — LLM analyzes what went well/poorly after each fight
- **Win/loss detection** — recognizes game-over and victory screens
- **Live dashboard** — React frontend with real-time logs, screen preview, and cost tracking

## Architecture

```
frontend/          React UI (Vite)
  src/App.jsx      Main dashboard — provider/model selection, live logs, preview

backend/           Python (FastAPI + asyncio)
  main.py          REST API + WebSocket server
  agent_loop.py    Core loop: capture → detect phase → LLM → verify → execute → reflect
  llm.py           LLM integration (Claude Code CLI, Antigravity CLI, Gemini CLI, Gemini API, OpenRouter)
  screen_capture.py  Screenshot capture, perceptual hashing, ruler drawing
  input_controller.py  Mouse/keyboard input via Windows APIs
  action_verifier.py   OpenCV template matching + OCR coordinate correction
  game_state.py    Phase detection, OCR state extraction, tiered memory
  logger.py        Session narration + execution logging
```

## Prerequisites

- **Windows 10/11**
- **Python 3.10+**
- **Node.js 18+**
- **Tesseract OCR** — [download installer](https://github.com/UB-Mannheim/tesseract/wiki). Install to the default path (`C:\Program Files\Tesseract-OCR\`)
- **One of the following LLM providers:**
  - [Codex CLI](https://developers.openai.com/codex/cli/) with ChatGPT sign-in (dashboard default).
  - Anthropic API — fastest option (one direct call per step, default `claude-haiku-5-5`); set `ANTHROPIC_API_KEY`, run `ant auth login`, or paste a key in the dashboard
  - [Claude Code CLI](https://claude.ai/code) installed and authenticated with your Claude plan
  - [Antigravity CLI](https://antigravity.google/) (`agy`) installed and authenticated (free)
  - [Gemini CLI](https://github.com/google-gemini/gemini-cli) installed and authenticated (free)
  - Gemini API key from [Google AI Studio](https://aistudio.google.com/)
  - OpenRouter API key from [openrouter.ai](https://openrouter.ai/)

## Setup

1. **Clone the repo**
   ```bash
   git clone https://github.com/ueyamaryuto412-blip/GameCLI-Agent.git
   cd GameCLI-Agent
   ```

2. **Install backend dependencies**
   ```bash
   cd backend
   pip install -r requirements.txt
   ```

3. **Install frontend dependencies**
   ```bash
   cd frontend
   npm install
   ```

4. **Create your game instructions**

   Create a file called `Game Instructions.md` in the project root:
   ```markdown
   Win a game of Slay The Spire
   ```
   This tells the agent what its objective is. Adjust for your game.

5. **Quick start** — run both backend and frontend:
   ```bash
   run.bat
   ```
   Or start them separately:
   ```bash
   # Terminal 1 — Backend
   cd backend
   python -m uvicorn main:app --host 127.0.0.1 --port 8000

   # Terminal 2 — Frontend
   cd frontend
   npm run dev
   ```

6. **Open the dashboard** at [http://localhost:5173](http://localhost:5173)

## Usage

1. Select your **LLM provider** and enter an API key (if required)
2. Choose a **model** (e.g. `gemini-2.5-flash`)
3. Pick an **agent role** (see below)
4. Select the **screen source** — a monitor or specific window
5. Click **Start** and switch to your game

The agent will:
- Capture screenshots and analyze the game state
- Decide which actions to take (play cards, click buttons, navigate menus)
- Execute actions with vision-verified precision
- Wait for opponent turns automatically
- Reflect after combats and adapt its strategy

## Agent Roles

Roles change **how** the agent interacts with the game — same core engine, different purpose.

| Role | Purpose | Behaviour |
|------|---------|-----------|
| **Gamer** | Entertainment / content creation | Plays to win with smart strategy. Narrates its thought process naturally. |
| **Reviewer** | Game critique / evaluation | Plays normally while evaluating design, UX, mechanics, difficulty, and polish. Notes positives and negatives. |
| **QA Tester** | Bug hunting / quality assurance | Systematically explores UI, tries edge cases, stress-tests interactions, and logs anomalies with repro steps. |
| **Speedrunner** | Optimization / efficiency | Minimizes actions and time. Skips optional content, prefers keyboard shortcuts, takes efficient paths. |

Select the role from the dashboard dropdown before starting the agent. The role shapes the LLM's system prompt — no code changes needed to switch between roles.

## Configuration

| Setting | Location | Description |
|---------|----------|-------------|
| Game objective | `Game Instructions.md` | What the agent should try to accomplish |
| Agent role | Dashboard UI | How the agent approaches the game (Gamer, Reviewer, Tester, Speedrunner) |
| LLM provider | Dashboard UI | Claude Code CLI, Antigravity CLI, Gemini CLI, Gemini API, or OpenRouter |
| Screen source | Dashboard UI | Which monitor or window to watch |
| API key | Dashboard UI | Entered at runtime, never stored on disk |

## Logs

Session logs are saved locally (git-ignored):
- `backend/Logs/` — detailed execution logs (vision verification, retries, timing)
- `backend/Narration/` — human-readable step-by-step narration of the agent's reasoning

## Tested With

- Slay the Spire (locally + GeForce Now Streaming)
- Heroes 3 HD (locally)
- Metroplex Zero (locally)
- Slay The Spire 2 (locally + GeForce Now Streaming)

The agent is designed to be game-agnostic for turn-based games, but has primarily been tested with Slay the Spire. Contributions are welcome.

## License

[MIT](LICENSE)
