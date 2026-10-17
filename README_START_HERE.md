# KEIBA CLOUD 統合パッケージ（2026-10-17）

## 重要：既存リポジトリは先に削除しない
現在の `state/keiba.sqlite3` は蓄積済みNARデータです。このZIPには含まれません。新規リポジトリを作ると、過去の収集データは自動では引き継がれません。先に既存のDBをバックアップしてください。GitHub上でリポジトリを削除すると履歴や設定も消えます。

## 収録内容
- `app.py`: NARの既存収集・レポート（実績あり）
- `jra_probe.py`: JRAの単一レースオッズ取得テスト（GitHub Actions実績あり）
- `jra_collector.py`: 指定したJRAレースIDの三連単オッズをDB保存（実運用未検証）
- `.github/workflows/main.yml`: NAR定期収集・スマホ向けActions Summary
- `.github/workflows/jra-test.yml`: JRA単発テスト
- `.github/workflows/jra-collect.yml`: JRA指定レース収集
- `tests/`: テストコード

## 新規リポジトリへの導入
1. GitHubで**Private**の新規リポジトリを作る。古いリポジトリは削除しない。
2. このZIPを解凍し、**中身をリポジトリ直下**にアップロードする（フォルダごと入れ子にしない）。`.github` は隠しフォルダなので注意。
3. Settings → Actions → General → Workflow permissions で必要なら Read and write permissions を許可する。
4. Settings → Secrets and variables → Actions → Variables に `JRA_RACE_IDS` = `202608040711` を追加。これは過去レースのテストIDであり、全開催の自動検出ではない。
5. Actionsで `Collect NAR data` と `JRA odds collection (experimental)` を手動実行し、**実行ログとSummaryの両方**を確認する。
6. 動作確認後に旧DBを移す場合は、新しい収集を止めてからDBの整合性を確認して移行する。

## 未完成・制限
JRA全レース自動検出、出馬表・結果・払戻、JRAの仮想購入・照合は未実装。NARの仮想購入も実績0件で、精度予測モデルは未実装。netkeibaの非公式取得先は変更や利用制限の可能性あり。自動取得は利用条件を確認すること。

**このZIPに実データのDBは入っていません。古いリポジトリの削除は推奨しません。**
