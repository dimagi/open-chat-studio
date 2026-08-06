# Open Chat Studio

<!-- hy-mt2-i18n:start -->
[English](./README.md) | [中文](./README_zh-CN.md) | **日本語** | [Español](./README_es.md)
<!-- hy-mt2-i18n:end -->

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/dimagi/open-chat-studio) [![codecov](https://codecov.io/github/dimagi/open-chat-studio/graph/badge.svg?token=SUKZMAWM3O)](https://codecov.io/github/dimagi/open-chat-studio)

Open Chat Studioは、AI駆動型のチャットアプリケーションを構築・デプロイ・評価するためのプラットフォームです。さまざまなLLM（大規模言語モデル）の利用、チャットボットの作成、会話の管理、そしてさまざまなメッセージングプラットフォームとの連携に役立つツールを提供しています。

[ユーザードキュメント](https://docs.openchatstudio.com) | [デベロッパードキュメント](https://developers.openchatstudio.com/)

## 貢献方法

Open Chat Studioへの貢献を心より歓迎します！貢献に興味がある方は、取り組み方に関する詳細な情報が記載されている[貢献ガイドライン](https://developers.openchatstudio.com/contributing/)をご覧ください。

## テクノロジースタック
- **バックエンド:** Python 3.13+、Django、Django REST Framework、Celery
- **データベース:** PostgreSQL（pgvectorを使用）
- **キャッシュ/メッセージブローカー:** Redis
- **フロントエンド:** TypeScript、CSS（[Tailwind](http://tailwindcss.com/) + [DaisyUI](https://daisyui.com/)）、HTMX、Alpine.js、webpack、[ReactJS](https://react.dev/)および特定コンポーネント向けの[React Flow](https://reactflow.dev/)
- **LLM連携:** OpenAI、Anthropic、Groq、Gemini、Azureなど
- **デプロイメント:** Docker、Heroku

## クイックスタートのセットアップ

Open Chat Studioでは、開発自動化のために[UV](https://docs.astral.sh/uv/getting-started/installation/)および[Invoke](https://www.pyinvoke.org/)を使用しています。

### 前提条件

- Python 3.13（推奨）
- Node.js >= 24.0.0
- DockerおよびDocker Compose

### 設定

```bash
git clone https://github.com/dimagi/open-chat-studio.git
cd open-chat-studio
uv venv --python 3.13
source.venv/bin/activate
uv sync
inv setup-dev-env   # フックをインストールし、サービスを起動し、データベースをマイグレートし、フロントエンドをビルドし、スーパーユーザーを作成する
./manage.py runserver
```

別のターミナルでCeleryを実行してください。これはLLMとのやり取りに必要です。

```bash
inv celery
```

手動操作のステップ、環境設定、トラブルシューティングを含む完全なセットアップ手順については、[ローカル開発セットアップガイド](https://developers.openchatstudio.com/getting-started/local-setup/)をご覧ください。

## Dockerのみを使用する開発環境

ホスト上でDjangoとCeleryを実行する代わりに、Dockerの中でフルスタックを動作させることもできます。その場合、ローカルにPythonやNodeをインストールする必要はありません。

```bash
cp.env.example.env   # 最低限、SECRET_KEY を設定する
docker compose build
docker compose up
```

完全なセットアップガイド、利用可能なサービス、便利なコマンド、およびトラブルシューティングについては、[Docker開発環境セットアップガイド](https://developers.openchatstudio.com/getting-started/docker-setup/)をご覧ください。

## 配置デプロイ

Herokuに独自の本番インスタンスをデプロイするには：

[![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://www.heroku.com/deploy?template=https://github.com/dimagi/open-chat-studio)

## ヘルプの入手方法

- **バグ報告と機能リクエスト:** [フィードバックの送り方](https://developers.openchatstudio.com/contributing/)
- **開発者向けドキュメント:** [developers.openchatstudio.com](https://developers.openchatstudio.com/)
- **ユーザー向けドキュメント:** [docs.openchatstudio.com](https://docs.openchatstudio.com)
