# GameCLI Agent

## AIゲーム観測室

WindowsのメインPCへの移行、フランクリンの基準音声、GPUでの試聴コマンドは[制作引き継ぎ](HANDOFF.md)にまとめています。

**昔の人が、今のゲームを遊んだら？**　歴史上の人物をモデルにしたAIが、実際にゲームを操作してその場で実況するチャンネルです。ゲームの面白さを通じて、その人が何に目を留め、どう判断し、予想が外れたらどうするかを一緒に見ます。

管理AIは初めて見る人にゲームと出演者を紹介し、人物の特徴を画面に3つ出します。本編はプレイAIが実況します。管理AIは最後に戻り、印象に残った一手とその結果を視聴者と振り返ります。編集では画面と発言のタイミングを合わせ、判断の理由や人物らしい一言を残します。

調査AIが人物ごとにプレイAIの日本語音声を2〜3候補から順位づけし、企画書に根拠資料・演技上の解釈・候補ごとの理由と速度を残します。本人の録音、声を聞いた人の証言、著作から読み取れる話法の順に手がかりを探します。採用企画の第一候補をGemini TTSによる収録後の音声生成に使います。

## 日本語の初見ゲーム実況

このフォークでは、ダッシュボードの初期設定を「Codex CLI (ChatGPT login)」と「初見実況」にしています。Codex CLI で `codex login` を実行して ChatGPT でサインインし、`codex login status` で確認してください。プレイ中の画面を画像として Codex に送信し、契約の利用枠を使います。

Windows で下記の依存関係を用意して `run.bat` を起動し、[ダッシュボード](http://localhost:5173) を開きます。Target Type で Window、Target Name でゲームのウィンドウを選びます。Game Instructions に今回の目的と操作説明を入れて Start を押してください。モデルは「Codex CLI default」のまま使えます。

ダッシュボードの初見実況では、その回の観察をもとに一手と日本語の台詞を生成し、時刻付きの結果を `backend/Narration/` に保存します。音声付きの動画を作るには、以下の収録コマンドを使います。

### 調査から完成動画まで続けて実行する

実況で扱うのはローカルのソロモード（CPU戦を含む）です。標準のプレイAIと収録前の操作確認では、画面から現在のモードと次の入力対象を判定します。一人用とオンラインが並ぶメニューでは一人用を選べます。オンライン中や判別不能時は入力を停止します。これはゲーム内の操作ルールで、PCのネットワーク設定は変更しません。

```powershell
.\.venv\Scripts\python.exe backend/run_episode.py --settings studio/research-nim.json --count 1
```

この例ではWindows版Python同梱の石取りゲームNimを起動し、開始操作と区切りを画面から調べます。Steamを使う場合は[利用規約の自動操作に関する条項](https://store.steampowered.com/subscriber_agreement/#4)を確認してください。

日本語のゲーム実況企画を調べ、おすすめ順にこのPCで試します。Steam版はインストール先と実際のウィンドウ所有プロセスを照合して起動します。収録プロフィールが未登録でも、ゲーム画面から操作方法と終了条件を組み立てます。画面のハイライトや選択位置など、一手で期待した変化を画像で確かめられた候補を採用します。全候補で失敗した場合は理由を残して終了します。

採用企画の人格・収録単位で1本を収録し、末尾の映像に予定の終了画面が写っているか確認してから編集します。結果の根拠と次回の開始地点は `ending.json`、全候補の採否と操作前後の画像は `recordings/show-…/` の `selection.json` と `candidate-番号/` に残ります。録画確定後の再検証・再編集には `backend/produce.py --resume 番組フォルダー` を使えます。

STS2に絞る場合は `--app-id 2868840 --count 1`、すでに調べた企画なら `--shortlist shortlist.json` を指定します。調査・動作確認のアダプターは `--settings`、収録と編集のアダプターは `--studio` で切り替えられます。Steam以外は既に開いた作品名と一致するウィンドウか、ローカルの `preparation.options.local_launchers` に作品ID別に登録した `window`・`process`・`argv` を利用します。

### ゲームと出演AIを調べて選ぶ

リポジトリ直下で次を実行すると、日本向けSteamストアの候補と登録済み作品を調べ、プレイAIの人格と組み合わせた企画をおすすめ順に3案出します。企画ごとに、性格が表れる具体的な選択、推薦理由、単発かシリーズか、1本の区切り、操作や見せ方の懸念を確認できます。

```powershell
.\.venv\Scripts\python.exe backend/research.py
```

出力先は実行ごとに異なる `episodes/research/日時-ID/` です。`shortlist.md` で候補と根拠の動画を比較し、採用した番号を収録に渡します。ゲーム設定がある作品はその設定で開始し、未登録なら作品名と一致するウィンドウを開くか、同じSteam App IDを持つゲーム設定を `--game` で指定します。

```powershell
.\.venv\Scripts\python.exe backend/produce.py --proposal "episodes/research/日時-ID/shortlist.json" --pick 1
```

ゲームを絞って人格を比べるなら `--app-id 2868840 --count 2`、企画の狙いを変えるなら `--brief "初見でも性格の差が伝わるパズル回"` を調査コマンドに足します。初期の探索範囲は日本向けSteamのローグライクデッキ構築・パズル・ターン制の売上上位と、Steam ID付きの登録済み作品です。発売済みのWindowsソロ対応作品を対象にします。

YouTubeは「作品名 実況」の検索でタイトルが一致した通常動画の表示回数・掲載時期・リンクを少数保存します。作品名の略称のみの動画などは漏れるため、視聴例として扱います。取得不能と該当例なしは別々に記録します。調査日時、全候補と取得データは `shortlist.json` に残ります。

[調査設定](studio/research.json) をコピーし `--settings` で指定すると、候補を探す `discovery`、視聴例を集める `audience`、企画を選ぶ `casting` を独立して交換できます。標準の企画担当は既存のChatGPTログインを使うCodex CLIです。

### ゲームと人格を指定して番組を作る

WindowsでCodex CLIにログインし、収録用依存関係と[Geminiの編集用認証](HANDOFF.md)をセットアップしたら、リポジトリ直下で実行します。

```powershell
.\.venv\Scripts\python.exe backend/produce.py --game sts2 --persona "用心深いのに、自分で思いついた作戦は一度試したい負けず嫌い。"
```

STS2は開いているウィンドウを使い、閉じていればインストール済みのSteam版を起動します。AIは実際にゲームを操作するのでゲーム側の自動セーブが働きます。1ランが基本で、短い動作確認には `--minutes 2` を付けられます。他のゲームは開いているウィンドウの正確なタイトルか、ゲーム設定JSONを `--game` に渡します。操作方法や終了条件は [STS2のゲーム設定](studio/games/sts2.json) と同じ形式で指定できます。

番組は「管理AIの挨拶 → 特徴3つを画面表示 → プレイAIの実況本編 → 管理AIの総評」の順です。

実行時に表示される `recordings/show-…/` に、確認用の `episode.mp4`、管理AIの紹介と総評を記した `script.json`、ゲーム・人格・アダプター設定の控え `production.json` が入ります。新規収録の元映像と台詞・操作ログは `raw/`、編集で生成したプレイAIのWAVは `player-audio/` に残ります。

| 変更したいもの | 変更箇所 |
|---|---|
| ゲームの説明・操作方法・終了条件・Steam起動 | `studio/games/*.json` |
| プレイAIの性格 | `--persona` |
| キャプチャ方式・ゲーム起動 | `recorder` |
| プレイAIのモデル・実況の指示・判断と操作の実装 | `player` |
| 管理AIの人格・紹介・総評の生成方法 | `host` |
| 編集時に生成するプレイAIと管理AIの音声 | `player_voice` ／ `host_voice` |
| 待ち時間のカット・実況で残す発言 | `editor` |
| 番組名・管理AIの姿・プロフィールの見た目 | `design` |
| 字幕・ミックス・プロフィール表示秒数・エンコーダー | `exporter` |

[標準スタジオ設定](studio/default.json) をコピーして `--studio ファイル名.json` で指定します。各欄は `factory`（Pythonの `モジュール:クラス`）とコンストラクターに渡す `options` を持ちます。標準の後生成には `show_adapters:SceneEdit` を使います。独自実装は [アダプターの入出力](backend/show_types.py) に合わせ、backend内またはPythonからインポートできる場所に置きます。素材どうしの時刻は元録画の秒数、カット表は半開区間のフレーム番号で受け渡します。

編集失敗後は `--resume` に番組フォルダーを指定すると、確定済みの収録を再利用します。別の設定を試すなら `--from-recording` に元録画フォルダーを指定すると、別の番組フォルダーに書き出します。標準設定では、既存収録の採用台詞もGeminiで吹き替えます。

```powershell
.\.venv\Scripts\python.exe backend/produce.py --resume "recordings/show-日時-ID"
.\.venv\Scripts\python.exe backend/produce.py --game sts2 --persona "買い物好きな慎重派" --from-recording recordings/sts2-20261122-221410-883821 --recorded-credit "VOICEVOX:ずんだもん"
```

### Slay the Spire 2 のサンプル撮影

このサンプルでは「VOICEVOX:ずんだもん」のノーマルを使います。本番チャンネルの出演者と声は未定です。初回はセットアップで音声モデル、辞書、CPU用ランタイムを取得します。STS2 のソロのランを開きます。
リポジトリ直下で次を実行すると、現在のゲーム画面を読み、日本語で実況して操作します。
通常の収録単位はソロの1ランです。進行中なら現在の部屋から続け、敗北か最終クリアを画面で確認したところで締めます。時間を区切って試す場合だけ `--minutes 6` のように上限を指定します。進行はゲーム側で自動保存されます。

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\backend\setup_zundamon.ps1
Set-Location backend
..\.venv\Scripts\python.exe sts2_pilot.py
```

`--observe` を付けると、今見えている場面の実況と録音・録画だけを試せます。
収録結果は `recordings/sts2-日時/pilot.mp4` です。STS2のウィンドウとゲーム音に
ずんだもんの声と字幕を重ねます。各戦闘では、カードを選ぶところから攻撃の着弾、敵ターン、次の手札までを元の速さで見せます。発話と操作が済み、同じ画面で次の判断を待つ時間だけを詰めます。末尾に音声クレジットを表示します。Windowsの日本語デスクトップ音声を使う場合は `--voice windows` を指定します。元映像と台詞・時刻も
同じフォルダーに残ります。YouTubeに公開する前に、このMP4を人間がレビューできます。

ライブ収録では、画面を見たプレイヤーが「どれを選ぶか」と「その場で言いたい一言」を同じ応答で決めます。台詞があれば実際に読み上げ、声が終わってからゲームに入力します。選択の結果は次の画面で確かめます。動画にはその場で鳴らした音声を使います。元の判断、予定操作、送信を試みた入力、次に見えた結果は `narration.json` に、音声は `spoken/` に保存されます。

カード報酬だけを収録するなど目的を変えたいときは `--objective "カード報酬を選んで、結果を確認したら終了"` を渡してください。候補自体はゲームを見てから選びます。店への寄り道、カード選び、ピンチでの守り方に関する好みは `Game Instructions.slaythespire2.md` で編集できます。

普段の収録は台本を指定せずに起動します。ずんだもんはその日の画面から気になることを見つけ、部屋をまたいでその選択の行方を追います。`episodes/01-ikari.md` は「怒り」を取った直後に一戦を収録した試作用の台本です。特定の回の企画を明示的に渡す場合だけ、次の形式を使えます。

```powershell
# backend フォルダーから実行
..\.venv\Scripts\python.exe sts2_pilot.py --minutes 24 --episode ../episodes/01-ikari.md
```

### 収録済みの1ランを再編集する

`narration.json` と `spoken/` がある収録では、リポジトリ直下から次を実行すると、その場の実況を使って `gameplay.mp4` を作ります。同じフォルダーの `gameplay.json` に元録画のどのフレームを残したかを記録します。

```powershell
.\.venv\Scripts\python.exe backend/sts2_pilot.py --edit recordings/sts2-20261123-000335-446288
```

### 編集台本で特定の場面を抜き出す

STS2の実況者は「買い物好きで、心配になると暗算を始める」性格です。店に行きたい理由、被弾の予想、決着後の財布といった、その回を通して追える話を組み立てます。プレイ中の人物設定は `Game Instructions.slaythespire2.md`、スライム戦の編集台本は `episodes/slime-shop.json` にあります。

リポジトリ直下で次を実行すると、ゲームを起動せずに再編集できます。台本の `start` と `end` は元映像の秒数、`text` は台詞と字幕です。空の台詞はゲーム音だけのカットです。必要に応じて `offset` で発声を遅らせ、`reading` で読みを指定できます。
ライブ収録を切り直す場合は、カットに `spoken_file`（例 `spoken/000.wav`）を指定すると、その場でしゃべった音声を使えます。先頭のカットの `title` は冒頭に表示されます。

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
