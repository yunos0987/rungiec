# セキュリティポリシー / Security Policy

**English summary:** Please report security vulnerabilities privately through GitHub's private vulnerability reporting (the "Report a vulnerability" button in the repository's Security tab). Do not open a public issue for a vulnerability. Security fixes are made for the latest release and for the `main` branch. The rest of this document is in Japanese.

## 脆弱性の報告方法

脆弱性は、公開の Issue や Pull Request では報告しないでください。GitHub の非公開の脆弱性報告（private vulnerability reporting）を使ってください。

1. リポジトリの **Security** タブを開く
2. **Report a vulnerability** を押す
3. 下記の内容を書いて送る

報告は管理者だけに届きます。修正が公開されるまで、報告内容は公開しないでください。

## 対象バージョン

セキュリティ修正の対象は、最新のリリースと `main` ブランチです。古いリリースへの修正の取り込み（バックポート）は予定していません。現在のバージョンは `rungiec.iec` の `RUNGIEC_VERSION_MAJOR` / `RUNGIEC_VERSION_MINOR` / `RUNGIEC_VERSION_REVISION` で確認できます。

## 報告に含めてほしい内容

- 影響を受ける RUNGIEC のバージョン、または commit ハッシュ
- 使った jiepp と jiecc のバージョン（`jiepp --version` / `jiecc --version` の出力）と OS
- 問題を再現できる最小の入力ファイルとコマンドライン
- 起きたことと、想定していた動作
- 分かる場合は、原因の見立てや修正案

## 対象範囲

RUNGIEC は jiepp のマクロのライブラリ（`rungiec.iec` / `rungiec_st.iec` / `rungiec_ld.iec`）と、テスト用の Python スクリプトです。次のような問題を、セキュリティ上の問題として扱います。

- RUNGIEC のマクロが、書いたラダー図と異なる動作の ST を、前処理エラーも警告も出さずに生成すること（安全回路などで実害につながりうるもの）
- `tools/` の Python スクリプトが、意図しないファイルを書き換える・消すこと

## 対象外

- jiepp と jiecc 自体の問題（それぞれの配布元に報告してください）
- [`docs/GUIDE.md` の「使い方の注意」](docs/GUIDE.md#使い方の注意)や `docs/DESIGN.md` に、検出しないと書いてある書き方による動作
- RUNGIEC が生成した ST を、利用者が別のツールや実機で使うときに起きる問題
