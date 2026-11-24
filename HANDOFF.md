# AIゲーム観測室｜メインPCへの引き継ぎ

## 本番のプレイAI音声 — Gemini 3.8 Flash TTS

収録PCはゲーム画面・音・観察時刻・操作・その場の台詞を記録する。編集PCは選んだ台詞をGeminiで合成し、実際の音声が場面に収まるものを使う。新しい企画では調査AIが声を選ぶ。旧フランクリン企画の仮配役はAlgenib。「印刷屋なら」のQwenテイクは聴き比べ用。管理AIの声はSupertonic F5。

メインPCの移行用ブランチで `git pull --ff-only` を実行し、制作環境に `.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt` で依存関係を入れる。認証はクレジット対象のプロジェクトに合わせて選ぶ。

- **AI StudioのGemini APIキー**　環境変数またはリポジトリ直下のローカル `.env` に `GEMINI_API_KEY` を設定する。
- **Google CloudのVertex AI**　対象プロジェクトでVertex AI APIを有効にして `gcloud auth application-default login` を実行し、環境変数 `GEMINI_TTS_VERTEX_PROJECT` にそのプロジェクトIDを設定する。この変数を指定するとVertex AIを使用する。

1台詞だけ生成して声と所要時間を確認する。

```powershell
.\.venv\Scripts\python.exe backend/gemini_tts.py --output recordings/gemini-franklin.wav
```

下記「既存の映像を再編集する場合」のZIPを展開してから、一戦を吹き替える。

```powershell
.\.venv\Scripts\python.exe backend/produce.py --proposal episodes/research/20261125-000329-franklin-voice/shortlist.json --from-recording recordings/franklin-trial-20261124-195630
```

同じ番組フォルダーで `--resume` すると、`player-audio/` の生成済みWAVを同じ声設定・台詞に再利用する。配役は `studio/default.json` のコピーで `player_voice.options.voice` を変更し、`--studio` で渡せる。作成済みのカスタム声ID（`voice_...`）も指定できる。[Geminiの音声仕様](https://aistudio.google.com/docs/speech-generation) · [Vertex AIの音声仕様](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/text-to-speech/overview)

## Qwenの比較音源とローカル試聴

フランクリン役に選んだ「印刷屋なら」の元音声は [reference.wav](studio/voices/franklin/reference.wav)（7.36秒）。これをお手本にして、同じ話者設定から生成した [剣が出た瞬間](studio/voices/franklin/clone-sword.wav) と [次の一手の予想](studio/voices/franklin/clone-count.wav) を同梱した。元音声は歴史上の本人を演じるために設計した合成音声。台詞、読み、演技指示、チェックサムは [reference.json](studio/voices/franklin/reference.json) にある。

WindowsのPowerShellで実行する。Python 3.12（64ビット）とGitを使う。

```powershell
git clone --branch codex/chatgpt-gameplay https://github.com/ueyamaryuto412-blip/GameCLI-Agent.git
cd GameCLI-Agent
python -m venv .venv-qwen
.\.venv-qwen\Scripts\python.exe -m pip install torch==2.10.0 torchaudio==2.10.0 --index-url https://download.pytorch.org/whl/cu128
.\.venv-qwen\Scripts\python.exe -m pip install qwen-tts==0.1.1
nvidia-smi
.\.venv-qwen\Scripts\python.exe -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
.\.venv-qwen\Scripts\python.exe backend/audition_franklin.py --device cuda --output-dir recordings/qwen-gpu-01 --text "おや、ビッグバンから剣が出た。" --text "私の勘定では、左の八は六になるはずだ。試してみよう。"
```

初回はQwenのBaseモデルを取得する。2本とも同じ参照WAVと読みから作った話者情報を使う。出力は番号付きWAVと `timings.json`。初期化時間、各台詞の生成秒数・音声秒数、GPU名、PyTorchが確保したメモリのピークを記録する。再計測時は新しい出力先を指定する。既に取得したモデルは `--model モデルのディレクトリ` でも指定できる。[Qwenの公式手順](https://github.com/QwenLM/Qwen3-TTS#voice-design-then-clone) · [PyTorchのWindows向け配布](https://pytorch.org/get-started/previous-versions/)

移行元のRyzen 9 7940HSのCPUでは、上記の2本にそれぞれ12.65秒（音声2.88秒）と19.74秒（音声4.80秒）かかった。[計測結果](studio/voices/franklin/cpu-trial.json)。5060 Tiの速度と、別の台詞でも同じ人に聞こえるかの採用判断はメインPCで行う。

## 番組として決まったこと

- 「昔の人が今のゲームを遊んだら？」がテーマ。管理AIがゲームと今回の人物を紹介し、プレイAIの特徴を3つ画面に出す。本編はプレイAIに任せ、管理AIは最後にその回の判断を振り返る。管理AIの声はSupertonic F5、速度0.97、長めの余韻。
- 題材は『Slay the Spire 2』のソロ。章の検証は第1章ボス撃破か途中敗北まで。フランクリンの人物像を試した回だけは一戦で終了した。その回の[人格と指示](studio/episodes/franklin-trial/persona.md)、[時刻付きの実際の45判断](studio/episodes/franklin-trial/narration.json)、[最新の調査企画](episodes/research/20261125-000329-franklin-voice/shortlist.md)を参照。プレイの既定はCodex CLIのAstra、effort high。
- 戦闘の展開とゲーム音を残し、判断待ちの空白を詰める。映像と声を見比べ、過去の場面の台詞が次の場面まで続くものを落とす。完成品は人間がレビューしてから投稿する。

## 制作環境と入口

リポジトリ直下で実行する。QwenのGPU環境と、ゲームの操作・収録に使う環境は別々に用意する。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt pytest
.\.venv\Scripts\python.exe -m pytest backend/tests -q
.\.venv\Scripts\python.exe backend/research.py --help
.\.venv\Scripts\python.exe backend/produce.py --help
codex login status
```

収録ではWindows上でゲーム画面が表示され、入力できる状態にする。Codex CLIはメインPCでChatGPTにログインする。既存のVOICEVOX音声を使う設定には `.\backend\setup_zundamon.ps1` を実行する。Supertonicのモデルは初回利用時に取得する。ダッシュボードを使う場合はNode.js 22.12以上で `npm.cmd ci --prefix frontend` を実行する。OCRはTesseractを使う。各モードの起動例は[README](README.md)にある。

| 作業 | 入口と現在の動作 |
| --- | --- |
| ゲーム・人物の調査 | `backend/research.py` と `studio/research.json`。SteamとYouTubeを調べ、企画と音声候補を出力。現行の調査AIはGeminiの声を順位づけする。 |
| 準備・収録・書き出し | `backend/run_episode.py` で調査から接続。`backend/produce.py` で既存企画・素材から制作。`studio/default.json` が管理AI、収録、編集、音声、書き出しの接続先。 |
| STS2の収録範囲 | `studio/games/sts2.json` は1ラン用。`studio/episodes/dr-claude-act1/` に第1章で終了する実績のある設定がある。このフォルダーには当時の声・管理AIの割り込み設定も残る。現在の番組設定は `studio/default.json` を参照。 |
| 音声と編集 | 既存収録は入力と発言を記録し、設定に応じて収録中に音声も生成する。`backend/scene_narration.py` が映像に収まる発言を選び、`backend/gameplay_seams.py` がカット前後の映像と音を処理する。 |
| 固定声の後生成 | `backend/audition_franklin.py` でローカル合成と計測が可能。本番の後生成はGeminiを使い、Qwenは比較用に残している。 |

## 既存の映像を再編集する場合

フランクリンの一戦を再編集するときは、メインPCのブラウザーで所有者として試写室にログインし、[編集用素材ZIP（487 MB）](https://spire-ai-screening-room.greyflute10.chatgpt.site/franklin/franklin-trial-20261124-195630-source.zip?download) をダウンロードする。下記は標準のダウンロード先に保存した場合。GameCLI-Agentのルートで実行すると、試走時と同じフォルダー構成になる。

```powershell
$zip = Join-Path $HOME 'Downloads/franklin-trial-20261124-195630-source.zip'
if ((Get-FileHash -LiteralPath $zip).Hash -ne 'a831f93d3780a101e966677ab696ff36a6964136a6154f162a0854a4c70e6b4b') { throw 'ZIPの内容を確認してください' }
Expand-Archive -LiteralPath $zip -DestinationPath .\recordings
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'backend'); from pathlib import Path; from produce import validate_source; print(len(validate_source(Path('recordings/franklin-trial-20261124-195630'))))"
```

検証結果は `45`。元映像 `game.mkv`、時刻付き台詞、`spoken/` の音声40本、各判断時のスクリーンショットを同梱している。旧PCの元素材の場所はOneDrive「ドキュメント」内の `ChatGPT/youtube creater/recordings/`。第1章の別収録も使う場合は下表の該当フォルダーをコピーする。

| フォルダー | 用途 |
| --- | --- |
| `franklin-trial-20261124-195630/` | フランクリン試走の `game.mkv`（約475 MB）、`narration.json`、`decisions.jsonl`、`spoken/`、編集済み動画。時刻と画面の対応の検証に使う。 |
| `show-dr-claude-act1-1795485465424-take2/` | 通し編集を確認した回。元素材は `raw/`（元映像約3.1 GB）。編集版や判断記録も同じ番組フォルダー内にある。 |

移行元の `production.json` は旧PCの絶対パスを保持する。別の配置で再編集するときは `produce.py --from-recording` にコピー先の素材フォルダーを指定すると、新しい制作ジョブになる。Sitesの視聴室とソースは所有者のSitesプロジェクトで管理している。[フランクリン試走](https://spire-ai-screening-room.greyflute10.chatgpt.site/franklin/) · [声の試聴室](https://spire-ai-screening-room.greyflute10.chatgpt.site/voices/#franklin-qwen)

## 次のセッションに渡す文

> メインPCのクレジット対象プロジェクトでGeminiの1台詞を試聴してください。その後、フランクリンの試走素材を使って一戦を吹き替え、声質、画面との同期、息継ぎを確認してください。管理AIの出番は冒頭と締めです。
