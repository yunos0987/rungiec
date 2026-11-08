# 変更履歴 / Changelog

0.2.0 以降の変更を記録します。0.2.0 は最初の公開版です。

## 0.4.0 (2026-11-09)

`RUNGIEC_VERSION` は 400 です。

### 互換性に影響する変更 / Breaking changes

- 必要なツールを jiepp 1.4.0 と Jiecc 9.4.0 に上げました。`rungiec.iec` の版の検査は、`_JIECC_FULL_VER` が 90400 未満なら PP91 `RUNGIEC needs Jiecc 9.4.0 or later` にします（jiepp だけで展開するときは確認しません）。マクロの展開結果と公開マクロは変わりません。ST モード自体は Jiecc 9.2.0 でも動きますが、確認しているのは 9.4.0 だけです。
- テストの枠組み jiecunit（git submodule の `vendor/jiecunit`）を v7.0.0 にしました。jiecunit v7.0.0 には Jiecc 9.4.0 が必要です。`jiecc --run` では、集計の後に `halt` でスキャンを止めて終わります。
- `rungiec.iec` / `rungiec_st.iec` / `rungiec_ld.iec` の前処理の条件式（`{#if}` / `{#elif}` と `__assert` の条件）を、jiepp 1.4.0 の論理演算子 `&&` / `||` / `!` で書き直しました。jiepp 1.2.0 ではこの表記が PP51 になります。展開結果は変わりません。lint の LN8 は `\and\` / `\or\` / `\not\` も指摘するようになりました（`&&` / `||` / `!` を書く）。

### テスト / Tests

- `f025` / `f026`（`FB_BOX` / `FBT_BOX` の入力ピン・出力ピンが BOOL でない）の期待を、Jiecc 9.4.0 の `jiecc --run` が実行の前に行う型検査のエラー（`TYPE_ERROR`）の文言に合わせました。

## 0.2.0 (2026-11-02)

最初の公開版です。IEC 61131-3 のラダー図 (LD) を jiepp のマクロで書き、ST 言語に展開します。同じソースから IEC 61131-10 の LD XML も作れます（LD モード）。

### 必要なツール / Requirements

- 展開に必要なのは jiepp 1.2.0 です（実行ファイルは https://github.com/yunos0987/jiepp/releases ）。テストの実行と IEC 61131-10 の XML への変換には Jiecc 9.2.0 を使います。jiecc のホームページ（https://www.graviness.com/iec_61131-3/jiecc.html ）の配布アーカイブ（Windows は `jiecc.zip`、Linux は `jiecc.tgz`）には両方が入っています。
- `rungiec.iec` は jiepp のビルトイン `__assert`、名前付きカウンタ（`__counter` / `__counter_peek` / `__counter_set`）、`__nosplit` を `__has_builtin` で確認し、無い jiepp では PP91 になります。
- LD モードには、さらに jiepp のビルトイン `__xmlattr` / `__xmltext` / `__arg_lhs` / `__arg_rhs` / `__arg_dir` が必要です。この 5 つは jiepp 1.2.0（と Jiecc 9.2.0 の配布アーカイブの jiepp）にあります。古い jiepp で使えるのは ST モードだけです。
- どちらのモードも Jiecc 9.2.0 が必要です。ST モードの文は POU の本体に `{st}` / `{end}` で囲まずに並びます。jiecc の版が 9.2.0 より古いと、`rungiec.iec` が PP91 `RUNGIEC needs Jiecc 9.2.0 or later` にします（jiepp だけで展開するときは確認しません）。
- テストランナー（`tools/run_tests.py`）には Python 3.7 以上が必要です（標準ライブラリのみ）。

### 公開マクロ / Public macros

公開マクロは 49 個です。形と意味は [`docs/REFERENCE.md`](docs/REFERENCE.md) にあります。

- 宣言と POU の構成: `RUNGIEC_VAR()` / `RUNGIEC_VAR_TEMP()` / `RUNGIEC_VAR_NOEDGE()`、`BEGIN_RUNGIEC()` / `END_RUNGIEC()`
- ネットワークと結合子: `RUNG`、`SERIES`、`PARALLEL`
- 一般形のネットワーク（直並列で書けないブリッジ回路など）: `BEGIN_NET()` / `WIRE(from, to, ...)` / `END_NET()`
- 接点: `CONTACT` / `NOT_CONTACT`、エッジ接点 `RISE_CONTACT` / `FALL_CONTACT` / `NOT_RISE_CONTACT` / `NOT_FALL_CONTACT`、比較接点 `EQ_CONTACT` / `NE_CONTACT` / `GT_CONTACT` / `GE_CONTACT` / `LT_CONTACT` / `LE_CONTACT`
- コイル: `COIL` / `NOT_COIL` / `SET_COIL` / `RESET_COIL` / `RISE_COIL` / `FALL_COIL`
- 関数・FB の呼び出し: `FB_SCAN` / `FB_IN` / `FB_BOX` / `FB_EXEC` / `FB_ENO` / `FUN_EXEC` / `FUN_EXEC_RET` / `FUN_ENO` / `FUN_ENO_RET`。`FB_BOX(inst, in_pin, out_pin, args...)` は IEC のボックスの形で、後段のパワーが出力 `inst.out_pin` になります。型名付きの `FBT_SCAN` / `FBT_IN` / `FBT_BOX` / `FBT_EXEC` / `FBT_ENO` は第 2 引数に FB の型名を取り、LD モードではボックスの `typeName` に出します（ST モードの動作は `FB_*` と同じです）
- インライン ST: `ST_EXEC(文...)`。パワー ON のときだけ文を実行します。文の中の裸のカンマ（`case N of 1, 2:` など）も書けます。
- 実行制御: 入れ子にできるマスタコントロール `BEGIN_MC(条件...)` / `END_MC()`、`RETURN()`、ジャンプ `JMP(ラベル)` とラベル `LABEL(ラベル)`。展開後の ST のジャンプは `goto` で、`jiecc -t codesys` では `jmp` です。`jiecc -t omron` / `-t keyence` / `-t mitsubishi` では `JMP()` と `LABEL()` は前処理エラーです。`JMP()` か `RETURN()` を持つ `WIRE` は一般形のネットワークの最後の `WIRE` に置きます。

### LD モード / LD mode

- `-D RUNGIEC_LD`（jiecc / jiepp）か、`{#include 'rungiec.iec'}` の前の `{#define RUNGIEC_LD 1}` で、公開マクロが ST の代わりに IEC 61131-10 の LD XML の `<Rung>` を出します。ソースは ST モードと同じです。変換は `jiecc examples/<名前>.iec -I . -D RUNGIEC_LD -P -nC -t omron -o <名前>.omron.ld.xml` の形で、`<BodyContent xsi:type="LD">` の XML になります。
- 接点・コイル・直列・並列・一般形のネットワーク・ボックス 14 種・`ST_EXEC`（Omron の `smcext:InlineST`）・`BEGIN_MC` / `END_MC`（`MC` / `MCR` の `Block`）・`JMP()` / `LABEL()` / `RETURN()` を、`LdObject` / `FbdObject` の要素と接続にします。`JMP()` / `LABEL()` は LD モードではどのターゲットでも使えます。
- POU の形は両モード共通で、`BEGIN_RUNGIEC()` が LD モードでは `{ld}` を出します。利用者が include するのは入口 `rungiec.iec` だけです。`rungiec.iec` は共通部を定義し、末尾でモードの本体（ST モードは `rungiec_st.iec`、LD モードは `rungiec_ld.iec`）を include します。公開マクロは本体だけが定義します。`rungiec_ld.iec` の後半は `tools/gen_arity_table.py --ld` の生成部分です。
- ボックスの呼び出しの引数は名前付きだけです（位置指定は jiepp の PP81）。入れ子の深さ（`RUNGIEC_NEST_MAX` は 16 以下）、1 か所に合流する出力の数（新しい設定 `RUNGIEC_LD_FANIN_MAX`、既定 16、1〜32）、要素でない子（要素の後ろに続く式 `CONTACT(A) or B` を含む）と、空のオペランド・ラベル（`CONTACT()` / `JMP()`）を前処理で検査します。`FB_*` の FB のボックスの `Block` の `typeName` は空で、`FBT_*` では引数の型名で出ます。LD XML を取り込むツールによっては、FB のボックスに型名と、後段へつながる出力が必須のことがあり、そのときは `FBT_BOX` を使います。

### 設定とバージョン / Settings and version

- 設定パラメータ `RUNGIEC_ARITY_MAX`（項数の上限）、`RUNGIEC_NEST_MAX`（ネストの上限）、`RUNGIEC_EDGE_MAX`（POU ごとのエッジのスロットの個数）、`RUNGIEC_NODE_MAX`（ネットワークの節点番号の上限）、`RUNGIEC_MC_MAX`（`BEGIN_MC` の入れ子の上限）、LD モードの `RUNGIEC_LD`（モードの切り替え）と `RUNGIEC_LD_FANIN_MAX`（合流の数の上限）。`{#define}` か `-D` で指定します。
- バージョンのマクロ `RUNGIEC_VERSION_MAJOR` / `RUNGIEC_VERSION_MINOR` / `RUNGIEC_VERSION_REVISION` / `RUNGIEC_VERSION`（0.2.0 では 200）。

### 検査 / Checks

- 項数、空の子、エッジのスロットの個数、一般形のネットワークと MC 領域の対と順序、`WIRE` の節点番号（0〜64 の整数リテラル）、`BEGIN_RUNGIEC()` と `END_RUNGIEC()` の対、`BEGIN_MC` / `END_MC` をまたぐジャンプと同じラベルの 2 回を前処理で検査し、誤りは jiepp の PP91 になります。
- 要素の中に `RUNG` などの文マクロを書いた入れ子は、ST モードでは実行時フォールト、LD モードでは前処理エラーになります。

### ラダー図 XML の変換器 / LD XML converter

- `tools/xml2rungiec.py` は IEC 61131-10 の LD XML（Omron Sysmac Studio の書き出しを含む）のラダー図を RUNGIEC の断片にし、`tools/rungiec2xml.py` は断片を標準の LD XML に戻します。ラングごとにアスキーアートのコメントを付け、変換できないラングは `(* @skipped N 理由 *)` のコメントで数えます。`tools/ld_xml_roundtrip.py` が往復を検査し、単体テストは `python -m unittest discover -s tools/ld_xml -p "test_*.py"` で実行します（標準ライブラリのみ。XSD の検査にだけ任意で lxml を使います）。

### テストと例題 / Tests and examples

- テスト 220 本（`tests/` の 208 本と、例題の確認 `tests/x_*.iec` の 12 本）と例題 12 本（`examples/`）。`python tools/run_tests.py` で実行します。ランナーは全例題を LD モードでも 4 ターゲットで変換し、期待値ファイル（`tests/expanded/<名前>.ld.xml`）との一致と、`tools/xml2rungiec.py` での往復を確認します。
- lint（`tools/lint_rungiec.py`。規則 LN0〜LN25）が、前処理と jiecc でエラーにならない書き間違い（最上位で `RUNG` を忘れた形、`RETURN()` と `JMP()` の位置、カンマの代わりの演算子、POU の中の順、ラベルの大文字小文字の違い、DSL を使う POU の `{st}` / `{end}` と `RUNGIEC_VAR*()` の前・`END_RUNGIEC()` の後の利用者の文、ボックスの位置指定の引数など）を検査します。`python tools/run_tests.py` がテストの前に実行し、`--no-lint` で省けます。
