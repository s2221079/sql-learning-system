from flask import Flask, request, render_template_string, redirect, url_for, session
import openai
import os
import sqlite3
from datetime import datetime
import re
import random
import openpyxl

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "s2221079")

# 安定版の初期化方法
openai.api_key = os.environ.get("OPENAI_API_KEY")

# データベース設定
DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    # PostgreSQL（本番環境）
    import psycopg2
    from psycopg2.extras import DictCursor
    
    # Render の postgres:// を postgresql:// に変換
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    
    def get_db_connection():
        return psycopg2.connect(DATABASE_URL)
    
    DB_TYPE = "postgresql"
    print("✅ PostgreSQL接続モード")
else:
    # SQLite（ローカル開発）
    DB_FILE = "学習履歴.db"
    
    def get_db_connection():
        return get_db_connection()
    
    DB_TYPE = "sqlite"
    print("✅ SQLite接続モード")

# データベース初期化
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if DB_TYPE == "postgresql":
        # PostgreSQL用のCREATE TABLE
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                problem_id TEXT NOT NULL,
                format TEXT,
                user_sql TEXT,
                user_explanation TEXT,
                sql_result TEXT,
                sql_feedback TEXT,
                meaning_result TEXT,
                meaning_feedback TEXT
            )
        ''')
    else:
        # SQLite用のCREATE TABLE
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                problem_id TEXT NOT NULL,
                format TEXT,
                user_sql TEXT,
                user_explanation TEXT,
                sql_result TEXT,
                sql_feedback TEXT,
                meaning_result TEXT,
                meaning_feedback TEXT
            )
        ''')
        
        # format列の追加チェック（SQLiteのみ）
        cursor.execute("PRAGMA table_info(logs)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'format' not in columns:
            cursor.execute('ALTER TABLE logs ADD COLUMN format TEXT')
            print("✅ format列を追加しました")
    
    conn.commit()
    conn.close()
    
# アプリ起動時にDBを初期化
init_db()

FORMATS = ["選択式", "穴埋め式", "記述式", "意味説明"]

# 8構文のリスト
TOPICS = ['SELECT', 'WHERE', 'ORDERBY', '集約関数', 'GROUPBY', 'HAVING', 'JOIN', 'サブクエリ']

# 構文説明の辞書
TOPIC_EXPLANATIONS = {
    'SELECT': '''
<h2>📚 SELECT句について</h2>
<p><strong>SELECT句</strong>は、データベースから取得したいデータの<strong>列（カラム）</strong>を指定する構文です。</p>

<h3>基本的な使い方:</h3>
<pre>SELECT 列名1, 列名2 FROM テーブル名;</pre>

<h3>主なポイント:</h3>
<ul>
    <li><strong>特定の列を取得:</strong> <code>SELECT name, age FROM users;</code></li>
    <li><strong>全ての列を取得:</strong> <code>SELECT * FROM users;</code></li>
    <li>列名はカンマ(,)で区切って複数指定できます</li>
</ul>

<h3>例:</h3>
<pre>SELECT id, name FROM employees;</pre>
<p>→ employeesテーブルからidとnameの列を取得します</p>
''',
    
    'WHERE': '''
<h2>📚 WHERE句について</h2>
<p><strong>WHERE句</strong>は、データの<strong>絞り込み条件</strong>を指定する構文です。</p>

<h3>基本的な使い方:</h3>
<pre>SELECT 列名 FROM テーブル名 WHERE 条件;</pre>

<h3>主なポイント:</h3>
<ul>
    <li><strong>比較演算子:</strong> =, >, <, >=, <=, <> (等しくない)</li>
    <li><strong>論理演算子:</strong> AND, OR, NOT</li>
    <li><strong>文字列の比較:</strong> シングルクォート(')で囲む</li>
    <li><strong>BETWEEN:</strong> 範囲指定（例: <code>WHERE age BETWEEN 20 AND 30</code>）</li>
    <li><strong>IN:</strong> 複数の値を指定（例: <code>WHERE department_id IN (1, 2, 3)</code>）</li>
    <li><strong>LIKE:</strong> パターンマッチング（例: <code>WHERE name LIKE '田%'</code>）</li>
</ul>

<h3>例:</h3>
<pre>SELECT name FROM employees WHERE salary > 50000;</pre>
<p>→ 給与が50000より大きい従業員の名前を取得します</p>

<pre>SELECT * FROM employees WHERE age BETWEEN 25 AND 35;</pre>
<p>→ 年齢が25歳から35歳の従業員を取得します</p>

<pre>SELECT * FROM employees WHERE department_id IN (1, 2, 3);</pre>
<p>→ 部署IDが1、2、3のいずれかの従業員を取得します</p>
''',
    
    'ORDERBY': '''
<h2>📚 ORDER BY句について</h2>
<p><strong>ORDER BY句</strong>は、取得したデータを<strong>並び替える</strong>構文です。</p>

<h3>基本的な使い方:</h3>
<pre>SELECT 列名 FROM テーブル名 ORDER BY 列名 [ASC|DESC];</pre>

<h3>主なポイント:</h3>
<ul>
    <li><strong>ASC:</strong> 昇順（小さい→大きい）※省略可能</li>
    <li><strong>DESC:</strong> 降順（大きい→小さい）</li>
    <li>複数の列で並び替え可能（カンマ区切り）</li>
</ul>

<h3>例:</h3>
<pre>SELECT name, salary FROM employees ORDER BY salary DESC;</pre>
<p>→ 給与の高い順に従業員を並び替えます</p>
''',
    
    '集約関数': '''
<h2>📚 集約関数について</h2>
<p><strong>集約関数</strong>は、複数行のデータを<strong>集計</strong>する関数です。</p>

<h3>主な集約関数:</h3>
<ul>
    <li><strong>COUNT():</strong> 行数をカウント</li>
    <li><strong>COUNT(DISTINCT 列名):</strong> 重複を除いた行数をカウント</li>
    <li><strong>SUM():</strong> 合計を計算</li>
    <li><strong>AVG():</strong> 平均を計算</li>
    <li><strong>MAX():</strong> 最大値を取得</li>
    <li><strong>MIN():</strong> 最小値を取得</li>
</ul>

<h3>例:</h3>
<pre>SELECT COUNT(*) FROM employees;</pre>
<p>→ 従業員の総数を取得します</p>

<pre>SELECT AVG(salary) FROM employees;</pre>
<p>→ 給与の平均値を計算します</p>

<pre>SELECT COUNT(DISTINCT department_id) FROM employees;</pre>
<p>→ 重複を除いた部署の数を取得します</p>
''',
    
    'GROUPBY': '''
<h2>📚 GROUP BY句について</h2>
<p><strong>GROUP BY句</strong>は、データを<strong>グループ化</strong>して集計する構文です。</p>

<h3>基本的な使い方:</h3>
<pre>SELECT 列名, 集約関数 FROM テーブル名 GROUP BY 列名;</pre>

<h3>主なポイント:</h3>
<ul>
    <li>GROUP BYで指定した列ごとにデータをまとめます</li>
    <li>集約関数と組み合わせて使います</li>
    <li>SELECT句に指定できるのは、GROUP BY句の列か集約関数のみ</li>
</ul>

<h3>例:</h3>
<pre>SELECT department_id, COUNT(*) FROM employees GROUP BY department_id;</pre>
<p>→ 部署ごとの従業員数を集計します</p>
''',
    
    'HAVING': '''
<h2>📚 HAVING句について</h2>
<p><strong>HAVING句</strong>は、<strong>グループ化後のデータ</strong>に条件を指定する構文です。</p>

<h3>基本的な使い方:</h3>
<pre>SELECT 列名, 集約関数 FROM テーブル名 GROUP BY 列名 HAVING 条件;</pre>

<h3>主なポイント:</h3>
<ul>
    <li>WHERE句はグループ化<strong>前</strong>、HAVING句はグループ化<strong>後</strong>の条件</li>
    <li>HAVING句では集約関数を使った条件を指定できます</li>
    <li>GROUP BY句と一緒に使います</li>
</ul>

<h3>例:</h3>
<pre>SELECT department_id, COUNT(*) FROM employees 
GROUP BY department_id HAVING COUNT(*) > 5;</pre>
<p>→ 従業員数が5人より多い部署のみを表示します</p>
''',
    
    'JOIN': '''
<h2>📚 JOIN句について</h2>
<p><strong>JOIN句</strong>は、<strong>複数のテーブルを結合</strong>する構文です。</p>

<div style="text-align: center; margin: 20px 0;">
    <img src="/static/images/join_diagram.png" alt="JOIN図解" style="max-width: 100%; height: auto; border: 2px solid #667eea; border-radius: 10px; box-shadow: 0 4px 8px rgba(0,0,0,0.1);">
</div>

<h3>基本的な使い方:</h3>
<pre>SELECT 列名 FROM テーブル1 JOIN テーブル2 ON 結合条件;</pre>

<h3>主なJOINの種類:</h3>
<ul>
    <li><strong>INNER JOIN:</strong> 両方のテーブルに存在するデータのみ</li>
    <li><strong>LEFT JOIN:</strong> 左テーブルの全データ + 右テーブルの一致データ</li>
    <li><strong>RIGHT JOIN:</strong> 右テーブルの全データ + 左テーブルの一致データ</li>
</ul>

<h3>例:</h3>
<pre>SELECT e.name, d.department_name 
FROM employees e JOIN departments d ON e.department_id = d.id;</pre>
<p>→ 従業員と所属部署の情報を結合して表示します</p>
''',
    
    'サブクエリ': '''
<h2>📚 サブクエリについて</h2>
<p><strong>サブクエリ</strong>は、<strong>SQL文の中に別のSQL文を入れ子にする</strong>構文です。</p>

<h3>基本的な使い方:</h3>
<pre>SELECT 列名 FROM テーブル名 WHERE 列名 IN (SELECT 列名 FROM テーブル名);</pre>

<h3>主なサブクエリの種類:</h3>
<ul>
    <li><strong>WHERE句のサブクエリ:</strong> 条件として別のクエリの結果を使用</li>
    <li><strong>FROM句のサブクエリ:</strong> 一時的なテーブルとして扱う</li>
    <li><strong>SELECT句のサブクエリ:</strong> 計算結果として使用</li>
</ul>

<h3>例:</h3>
<pre>SELECT name FROM employees 
WHERE department_id IN (SELECT id FROM departments WHERE location = 'Tokyo');</pre>
<p>→ 東京にある部署に所属する従業員を取得します</p>
'''
}

def get_time_elapsed():
    """学習時間を正確に計測（ブラウザを閉じても対応）"""
    user_id = session.get('user_id', 'unknown')
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 日付が変わったらリセット
    if session.get('learning_date') != today:
        session['learning_date'] = today
        session['accumulated_minutes'] = 0
        session['current_session_start'] = None
    
    # 現在のセッションが開始されているか確認
    if session.get('current_session_start') is None:
        session['current_session_start'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if 'accumulated_minutes' not in session:
            session['accumulated_minutes'] = 0
        return session['accumulated_minutes']
    
    # 現在のセッションの経過時間を計算
    start = datetime.strptime(session['current_session_start'], "%Y-%m-%d %H:%M:%S")
    now = datetime.now()
    current_session_minutes = int((now - start).total_seconds() / 60)
    
    # 累積時間 + 現在のセッション時間
    total_minutes = session.get('accumulated_minutes', 0) + current_session_minutes
    return total_minutes

def end_current_session():
    """現在のセッションを終了して累積時間に加算"""
    if session.get('current_session_start'):
        start = datetime.strptime(session['current_session_start'], "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        session_minutes = int((now - start).total_seconds() / 60)
        
        # 累積時間に加算
        session['accumulated_minutes'] = session.get('accumulated_minutes', 0) + session_minutes
        session['current_session_start'] = None
        print(f"✅ セッション終了: {session_minutes}分 (累積: {session['accumulated_minutes']}分)")

def get_time_display():
    """学習時間を時間:分形式で返す"""
    elapsed_minutes = get_time_elapsed()
    hours = elapsed_minutes // 60
    minutes = elapsed_minutes % 60
    return hours, minutes, elapsed_minutes

def get_progress_percentage(elapsed_minutes, target_minutes=480):
    """進捗パーセンテージを計算（デフォルト8時間=480分）"""
    percentage = min((elapsed_minutes / target_minutes) * 100, 100)
    return round(percentage, 1)

def load_problems(sheet_name):
    try:
        wb = openpyxl.load_workbook("problems.xlsx")
        ws = wb[sheet_name]
        problems = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row[0]:
                continue
            problem = {
                "id": str(row[0]) if row[0] else "",
                "title": str(row[1]) if row[1] else "",
                "answer_sql": str(row[2]) if row[2] else "",
                "explanation": str(row[3]) if row[3] else "",
                "choices": [str(row[4]) if row[4] else "", str(row[5]) if row[5] else "", str(row[6]) if row[6] else ""],
                "blank_template": str(row[7]) if len(row) > 7 and row[7] else None,
                "blank_answer": str(row[8]) if len(row) > 8 and row[8] else None
            }
            problems.append(problem)
        return problems
    except Exception as e:
        print(f"Excel読み込みエラー: {e}")
        return []

def normalize_sql_strict(sql):
    sql = sql.lower()
    sql = sql.strip()
    sql = sql.rstrip(";")
    sql = re.sub(r'[\n\r\t]+', ' ', sql)
    sql = re.sub(r'\s+', ' ', sql)
    sql = re.sub(r'\s*,\s*', ', ', sql)
    sql = re.sub(r'\(\s+', '(', sql)
    sql = re.sub(r'\s+\)', ')', sql)
    return sql

def extract_topic_from_problem_id(problem_id):
    """問題IDから構文名を抽出"""
    if '_' in problem_id:
        prefix = problem_id.split('_')[0].upper()
        prefix_to_topic = {
            'SELECT': 'SELECT',
            'WHERE': 'WHERE',
            'ORDERBY': 'ORDERBY',
            'AGG': '集約関数',
            'GROUPBY': 'GROUPBY',
            'HAVING': 'HAVING',
            'JOIN': 'JOIN',
            'SUBQUERY': 'サブクエリ'
        }
        return prefix_to_topic.get(prefix, 'SELECT')
    return 'SELECT'

def evaluate_sql(user_sql, correct_sql, format, problem=None, enable_gpt_feedback=True):
    """SQL評価関数"""
    user_sql = user_sql.lower().strip().rstrip(";")
    correct_sql = correct_sql.lower().strip().rstrip(";")

    if format == "穴埋め式" and problem and problem.get("blank_template") and problem.get("blank_answer"):
        user_answer = re.sub(r'\s+', '', user_sql.lower().strip())
        correct_answer = re.sub(r'\s+', '', problem["blank_answer"].lower().strip())
        if user_answer == correct_answer:
            return "正解 ✅", "完璧です！"
        else:
            if enable_gpt_feedback:
                return "不正解 ❌", f"正解は「{problem['blank_answer']}」です。"
            else:
                return "不正解 ❌", ""
    
    if format == "選択式":
        if user_sql == correct_sql:
            return "正解 ✅", "完璧なSQL文です！"
        else:
            if enable_gpt_feedback:
                return "不正解 ❌", "SQL文が正しくありません。"
            else:
                return "不正解 ❌", ""
    
    if format == "記述式":
        user_sql_normalized = normalize_sql_strict(user_sql)
        correct_sql_normalized = normalize_sql_strict(correct_sql)
        
        if user_sql_normalized == correct_sql_normalized:
            return "正解 ✅", "完璧なSQL文です！"
        
        if not enable_gpt_feedback:
            return "不正解 ❌", ""
        
        if 'where' in correct_sql_normalized and 'where' not in user_sql_normalized:
            return "不正解 ❌", "WHERE句が欠けています。条件を指定するには WHERE を使用してください。"
        
        if 'from' not in user_sql_normalized:
            return "不正解 ❌", "FROM句が欠けています。テーブル名を指定してください。"
        
        if not user_sql_normalized.startswith('select'):
            return "不正解 ❌", "SQL文はSELECTから始まる必要があります。"
        
        topic = "SQL"
        if problem and problem.get('id'):
            topic = extract_topic_from_problem_id(problem['id'])
        
        try:
            if os.environ.get("OPENAI_API_KEY"):
                problem_title = problem.get('title', '') if problem else ''
                
                prompt = f"""あなたはSQL学習システムの評価者です。初学者が書いたSQL文を評価してください。

【最重要ルール】
1. 学習者の回答が問題の要求を満たしていれば「正解」とする
2. 正解例と書き方が違っても、同じ結果が得られるなら「正解」
3. エイリアス名（a, b, e, d など）の違いは無視する
4. 列の順序の違いは無視する
5. 空白やセミコロンの有無は無視する

【学習中の構文】
{topic}

【問題文】
{problem_title}

【評価対象】
正解例: {correct_sql_normalized}
学習者のSQL: {user_sql_normalized}

【評価基準】
■ 正解 ✅（以下のいずれかを満たせば正解）
- 正解例と完全に一致する
- 正解例と異なるが、同じ結果が得られる
- エイリアス名が異なるだけ（a → e など）
- 列の順序が異なるだけ
- 大文字小文字のみが異なる

■ 部分正解 ⚠️
- SQL構文は正しいが、問題の要求の一部のみを満たしている

■ 不正解 ❌
- SQL構文エラー
- 問題文の要求を満たしていない

【フィードバックの絶対ルール】
1. 正しく書けている部分を必ず最初に褒める
2. 問題文に書かれていない要求は**絶対に**しない
3. エイリアス名の違いは指摘しない
4. 励ましの言葉を含める

【出力形式】
判定結果: 正解/部分正解/不正解
フィードバック: （建設的で具体的なアドバイス）"""
                
                response = openai.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    temperature=0.3,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=250
                )
                text = response['choices'][0]['message']['content'].strip()
                
                result_match = re.search(r"判定結果[:：]\s*(正解|部分正解|不正解)", text)
                feedback_match = re.search(r"フィードバック[:：]\s*(.*)", text, re.DOTALL)
                
                result = result_match.group(1) if result_match else "不正解"
                feedback = feedback_match.group(1).strip() if feedback_match else "SQL文が正しくありません。"
                
                if result == "正解":
                    result = "正解 ✅"
                elif result == "部分正解":
                    result = "部分正解 ⚠️"
                else:
                    result = "不正解 ❌"
                
                return result, feedback
        except Exception as e:
            print(f"OpenAI API エラー: {e}")
    
    if user_sql == correct_sql:
        return "正解 ✅", "完璧なSQL文です！"
    
    if enable_gpt_feedback:
        return "不正解 ❌", "SQL文が正しくありません。"
    else:
        return "不正解 ❌", ""

def evaluate_meaning(user_explanation, correct_explanation, enable_gpt_feedback=True, problem=None):
    """意味説明評価関数"""
    print(f"🔍 evaluate_meaning 開始")
    print(f"   enable_gpt_feedback={enable_gpt_feedback}")
    print(f"   user_explanation={user_explanation[:50]}...")
    
    if not user_explanation.strip():
        if enable_gpt_feedback:
            return "不正解 ❌", "説明が入力されていません。"
        else:
            return "不正解 ❌", ""
    
    user_explanation = user_explanation.strip()
    
    topic = "SQL"
    if problem and problem.get('id'):
        topic = extract_topic_from_problem_id(problem['id'])
    
    # APIキーチェック
    api_key = os.environ.get("OPENAI_API_KEY")
    print(f"   OPENAI_API_KEY exists: {bool(api_key)}")
    
    if not api_key:
        print("❌ OPENAI_API_KEY が設定されていません")
        if enable_gpt_feedback:
            return "不正解 ❌", "システムエラー: APIキーが設定されていません。"
        else:
            return "不正解 ❌", ""
    
    try:
        print(f"   OpenAI API呼び出し開始...")
        problem_title = problem.get('title', '') if problem else ''
        sql_text = problem.get('answer_sql', '') if problem else ''
        
        prompt = f"""あなたはSQL学習システムの評価者です。初学者によるSQL文の意味説明を評価してください。

【最重要ルール】
1. 学習者の説明が正解例と意味が同じなら、改善点を一切指摘しない
2. 正解例に書かれている内容を学習者も書いているなら、「欠けている」と言わない
3. 細かい言い回しの違いは完全に無視する
4. 「列」と「行」、「取得」と「表示」などの同義語は区別しない

【学習中の構文】
{topic}

【問題で提示されたSQL文】
{sql_text}

【評価対象】
正解例の説明: {correct_explanation}
学習者の説明: {user_explanation}

【評価基準】
■ 正解 ✅
学習者の説明に以下が含まれていれば正解:
- テーブル名
- 取得する列（またはグループ化の内容）
- 条件（WHERE、HAVINGなど）

**重要**: 上記が含まれていれば、表現が違っても正解とする

■ 部分正解 ⚠️
上記の要素が本当に欠けている場合のみ

■ 不正解 ❌
SQL文の動作を誤解している

【フィードバックの絶対ルール】
正解の場合は改善点を指摘せず、以下のように褒めるだけ:
「完璧です！」「素晴らしい理解ですね！」「その通りです！」

部分正解・不正解の場合のみ、本当に欠けている要素を指摘する

【出力形式】
判定結果: 正解/部分正解/不正解
フィードバック: （建設的なアドバイス）"""
        
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            temperature=0.1,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=250
        )
        
        print(f"   ✅ OpenAI API呼び出し成功")
        
        text = response['choices'][0]['message']['content'].strip()
        print(f"   GPT応答: {text[:100]}...")
        
        result_match = re.search(r"判定結果[:：]\s*(正解|部分正解|不正解)", text)
        feedback_match = re.search(r"フィードバック[:：]\s*(.*)", text, re.DOTALL)
        result = result_match.group(1) if result_match else "不正解"
        feedback = feedback_match.group(1).strip() if feedback_match else "説明が不十分です。"
        
        print(f"   判定結果: {result}")
        
        if result == "正解":
            result = "正解 ✅"
        elif result == "部分正解":
            result = "部分正解 ⚠️"
        else:
            result = "不正解 ❌"
        
        # グループBの場合はフィードバックを空にする
        if not enable_gpt_feedback:
            print(f"   グループB: フィードバックを空にします")
            return result, ""
        
        return result, feedback
        
    except Exception as e:
        print(f"❌ OpenAI API エラー: {e}")
        print(f"   エラー詳細: {type(e).__name__}")
        import traceback
        traceback.print_exc()
    
    # APIエラー時のフォールバック
    print(f"   フォールバックに到達")
    if enable_gpt_feedback:
        return "不正解 ❌", "説明が不十分です。"
    else:
        return "不正解 ❌", ""

def save_log(user_id, problem_id, format, user_sql, user_explanation, sql_result, sql_feedback, exp_result, exp_feedback):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        print(f"🔍 ログ保存開始: user_id={user_id}, problem_id={problem_id}, format={format}")
        print(f"   DB_TYPE={DB_TYPE}")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if DB_TYPE == "postgresql":
            query = '''
                INSERT INTO logs (user_id, timestamp, problem_id, format, user_sql, user_explanation, 
                                sql_result, sql_feedback, meaning_result, meaning_feedback)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            '''
        else:
            query = '''
                INSERT INTO logs (user_id, timestamp, problem_id, format, user_sql, user_explanation, 
                                sql_result, sql_feedback, meaning_result, meaning_feedback)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            '''
        
        print(f"   クエリ実行中...")
        cursor.execute(query, (user_id, timestamp, problem_id, format, user_sql, user_explanation, 
                              sql_result, sql_feedback, exp_result, exp_feedback))
        
        print(f"   コミット中...")
        conn.commit()
        
        print(f"   接続クローズ中...")
        conn.close()
        
        print(f"✅ ログ書き込み成功: {timestamp} (User: {user_id}, Format: {format})")
        
    except Exception as e:
        print(f"❌ ログ書き込み失敗: {e}")
        import traceback
        traceback.print_exc()

def get_user_statistics(user_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if DB_TYPE == "postgresql":
            cursor.execute('SELECT * FROM logs WHERE user_id = %s', (user_id,))
        else:
            cursor.execute('SELECT * FROM logs WHERE user_id = ?', (user_id,))
        total_count = cursor.fetchone()[0]
        
        if total_count == 0:
            conn.close()
            return None
        
        if DB_TYPE == "postgresql":
            cursor.execute('''
                SELECT COUNT(*) FROM logs 
                WHERE user_id = %s 
                AND (sql_result = '正解 ✅' OR meaning_result = '正解 ✅')
            ''', (user_id,))
        else:
            cursor.execute('''
                SELECT COUNT(*) FROM logs 
                WHERE user_id = ? 
                AND (sql_result = '正解 ✅' OR meaning_result = '正解 ✅')
            ''', (user_id,))
        correct_count = cursor.fetchone()[0]
        
        if DB_TYPE == "postgresql":
            cursor.execute('''
                SELECT COUNT(*) FROM logs 
                WHERE user_id = %s 
                AND (sql_result = '部分正解 ⚠️' OR meaning_result = '部分正解 ⚠️')
            ''', (user_id,))
        else:
            cursor.execute('''
                SELECT COUNT(*) FROM logs 
                WHERE user_id = ? 
                AND (sql_result = '部分正解 ⚠️' OR meaning_result = '部分正解 ⚠️')
            ''', (user_id,))
        partial_count = cursor.fetchone()[0]
        
        if DB_TYPE == "postgresql":
            cursor.execute('''
                SELECT COUNT(*) FROM logs 
                WHERE user_id = %s 
                AND (sql_result = '不正解 ❌' OR meaning_result = '不正解 ❌')
            ''', (user_id,))
        else:
            cursor.execute('''
                SELECT COUNT(*) FROM logs 
                WHERE user_id = ? 
                AND (sql_result = '不正解 ❌' OR meaning_result = '不正解 ❌')
            ''', (user_id,))
        incorrect_count = cursor.fetchone()[0]
        
        overall_accuracy = (correct_count / total_count * 100) if total_count > 0 else 0
        
        format_stats = {}
        for format_name in ['選択式', '穴埋め式', '記述式', '意味説明']:
            if DB_TYPE == "postgresql":
                cursor.execute('''
                    SELECT COUNT(*) FROM logs 
                    WHERE user_id = %s AND format = %s
                ''', (user_id, format_name))
            else:
                cursor.execute('''
                    SELECT COUNT(*) FROM logs 
                    WHERE user_id = ? AND format = ?
                ''', (user_id, format_name))
            format_total = cursor.fetchone()[0]
            
            if format_total > 0:
                if DB_TYPE == "postgresql":
                    cursor.execute('''
                        SELECT COUNT(*) FROM logs 
                        WHERE user_id = %s AND format = %s
                        AND (sql_result = '正解 ✅' OR meaning_result = '正解 ✅')
                    ''', (user_id, format_name))
                else:
                    cursor.execute('''
                        SELECT COUNT(*) FROM logs 
                        WHERE user_id = ? AND format = ?
                        AND (sql_result = '正解 ✅' OR meaning_result = '正解 ✅')
                    ''', (user_id, format_name))
                format_correct = cursor.fetchone()[0]
                
                format_accuracy = (format_correct / format_total * 100)
                format_stats[format_name] = {
                    'total': format_total,
                    'correct': format_correct,
                    'accuracy': round(format_accuracy, 1)
                }
            else:
                format_stats[format_name] = {
                    'total': 0,
                    'correct': 0,
                    'accuracy': 0
                }
        
        if DB_TYPE == "postgresql":
            cursor.execute('''
                SELECT timestamp, problem_id, sql_result, meaning_result 
                FROM logs 
                WHERE user_id = %s 
                ORDER BY timestamp DESC 
                LIMIT 10
            ''', (user_id,))
        else:
            cursor.execute('''
                SELECT timestamp, problem_id, sql_result, meaning_result 
                FROM logs 
                WHERE user_id = ? 
                ORDER BY timestamp DESC 
                LIMIT 10
            ''', (user_id,))
        recent_logs = cursor.fetchall()
        
        conn.close()
        
        return {
            'total_count': total_count,
            'correct_count': correct_count,
            'partial_count': partial_count,
            'incorrect_count': incorrect_count,
            'overall_accuracy': round(overall_accuracy, 1),
            'format_stats': format_stats,
            'recent_logs': recent_logs
        }
    except Exception as e:
        print(f"統計情報取得エラー: {e}")
        import traceback
        traceback.print_exc()
        return None

def get_detailed_statistics(user_id):
    """構文別・形式別の詳細統計を取得"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        placeholder = '%s' if DB_TYPE == "postgresql" else '?'
        
        topics = ['SELECT', 'WHERE', 'ORDERBY', '集約関数', 'GROUPBY', 'HAVING', 'JOIN', 'サブクエリ']
        formats = ['選択式', '穴埋め式', '記述式', '意味説明']
        
        detailed_stats = {}
        
        for topic in topics:
            detailed_stats[topic] = {}
            
            topic_prefix_map = {
                'SELECT': 'SELECT_',
                'WHERE': 'WHERE_',
                'ORDERBY': 'ORDERBY_',
                '集約関数': 'AGG_',
                'GROUPBY': 'GROUPBY_',
                'HAVING': 'HAVING_',
                'JOIN': 'JOIN_',
                'サブクエリ': 'SUBQUERY_'
            }
            
            prefix = topic_prefix_map.get(topic, f"{topic}_")
            
            for format_name in formats:
                cursor.execute(f'''
                    SELECT COUNT(*) FROM logs 
                    WHERE user_id = {placeholder} AND problem_id LIKE {placeholder} AND format = {placeholder}
                ''', (user_id, f"{prefix}%", format_name))
                total = cursor.fetchone()[0]
                
                if total > 0:
                    cursor.execute(f'''
                        SELECT COUNT(*) FROM logs 
                        WHERE user_id = {placeholder} AND problem_id LIKE {placeholder} AND format = {placeholder}
                        AND (sql_result = '正解 ✅' OR meaning_result = '正解 ✅')
                    ''', (user_id, f"{prefix}%", format_name))
                    correct = cursor.fetchone()[0]
                    
                    accuracy = (correct / total * 100)
                    detailed_stats[topic][format_name] = {
                        'total': total,
                        'correct': correct,
                        'accuracy': round(accuracy, 1)
                    }
                else:
                    detailed_stats[topic][format_name] = {
                        'total': 0,
                        'correct': 0,
                        'accuracy': 0
                    }
        
        conn.close()
        return detailed_stats
    except Exception as e:
        print(f"詳細統計取得エラー: {e}")
        import traceback
        traceback.print_exc()
        return {}

def is_test_mode():
    """テストモードかどうかを判定"""
    return session.get('test_mode', False)

def get_format_question_threshold(format=None):
    """形式変更までの問題数"""
    # テストモード
    if is_test_mode():
        return 2
    
    # 通常モード
    if format in ['記述式', '意味説明']:
        return 3  # 記述式・意味説明は3問
    else:
        return 5  # 選択式・穴埋め式は5問

def get_recent_accuracy(user_id, topic, format, limit=5, start_time=None):
    # テストモード、または形式に応じたlimitを設定
    if is_test_mode():
        limit = 2
    elif format in ['記述式', '意味説明']:
        limit = 3
    else:
        limit = 5
    
    topic_prefix_map = {
        'SELECT': 'SELECT_',
        'WHERE': 'WHERE_',
        'ORDERBY': 'ORDERBY_',
        '集約関数': 'AGG_',
        'GROUPBY': 'GROUPBY_',
        'HAVING': 'HAVING_',
        'JOIN': 'JOIN_',
        'サブクエリ': 'SUBQUERY_'
    }
    
    prefix = topic_prefix_map.get(topic, f"{topic}_")
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        placeholder = '%s' if DB_TYPE == "postgresql" else '?'
        
        if start_time:
            cursor.execute(f'''
                SELECT sql_result, meaning_result 
                FROM logs 
                WHERE user_id = {placeholder} AND problem_id LIKE {placeholder} AND format = {placeholder} AND timestamp >= {placeholder}
                ORDER BY timestamp DESC 
                LIMIT {placeholder}
            ''', (user_id, f"{prefix}%", format, start_time, limit))
        else:
            cursor.execute(f'''
                SELECT sql_result, meaning_result 
                FROM logs 
                WHERE user_id = {placeholder} AND problem_id LIKE {placeholder} AND format = {placeholder}
                ORDER BY timestamp DESC 
                LIMIT {placeholder}
            ''', (user_id, f"{prefix}%", format, limit))
        
        results = cursor.fetchall()
        conn.close()
        
        if not results:
            return None
        
        correct_count = 0
        for sql_result, meaning_result in results:
            if sql_result == '正解 ✅' or meaning_result == '正解 ✅':
                correct_count += 1
        
        accuracy = (correct_count / len(results)) * 100
        return {
            'total': len(results),
            'correct': correct_count,
            'accuracy': round(accuracy, 1)
        }
    except Exception as e:
        print(f"正答率計算エラー: {e}")
        import traceback
        traceback.print_exc()
        return None

def get_next_format(current_format, accuracy):
    formats = ['選択式', '穴埋め式', '記述式', '意味説明']
    current_index = formats.index(current_format)
    
    if accuracy >= 80:
        next_index = min(current_index + 1, len(formats) - 1)
        return formats[next_index]
    elif accuracy >= 70:
        return current_format
    else:
        next_index = max(current_index - 1, 0)
        return formats[next_index]

def get_learning_progress(user_id):
    """学習進捗を取得"""
    progress = session.get('learning_progress', {
        'current_topic': 'SELECT',
        'current_format': '選択式',
        'topic_index': 0
    })
    return progress

def update_learning_progress(user_id, topic, format):
    """学習進捗を更新"""
    progress = session.get('learning_progress', {})
    progress['current_topic'] = topic
    progress['current_format'] = format
    progress['format_question_count'] = 0
    progress['format_start_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    if topic in TOPICS:
        progress['topic_index'] = TOPICS.index(topic)
    
    session['learning_progress'] = progress

def get_topic_overall_accuracy(user_id, topic, format):
    """その構文・形式での全体の正答率を計算"""
    topic_prefix_map = {
        'SELECT': 'SELECT_',
        'WHERE': 'WHERE_',
        'ORDERBY': 'ORDERBY_',
        '集約関数': 'AGG_',
        'GROUPBY': 'GROUPBY_',
        'HAVING': 'HAVING_',
        'JOIN': 'JOIN_',
        'サブクエリ': 'SUBQUERY_'
    }
    
    prefix = topic_prefix_map.get(topic, f"{topic}_")
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        placeholder = '%s' if DB_TYPE == "postgresql" else '?'
        
        cursor.execute(f'''
            SELECT sql_result, meaning_result 
            FROM logs 
            WHERE user_id = {placeholder} AND problem_id LIKE {placeholder} AND format = {placeholder}
            ORDER BY timestamp DESC
        ''', (user_id, f"{prefix}%", format))
        
        results = cursor.fetchall()
        conn.close()
        
        if not results:
            return None
        
        correct_count = 0
        for sql_result, meaning_result in results:
            if sql_result == '正解 ✅' or meaning_result == '正解 ✅':
                correct_count += 1
        
        accuracy = (correct_count / len(results)) * 100
        return {
            'total': len(results),
            'correct': correct_count,
            'accuracy': round(accuracy, 1)
        }
    except Exception as e:
        print(f"正答率計算エラー: {e}")
        import traceback
        traceback.print_exc()
        return None

def get_completed_formats(user_id):
    """ユーザーがこれまでに通過した構文と形式を取得"""
    if 'completed_formats' not in session:
        session['completed_formats'] = {}
    return session['completed_formats']

def add_completed_format(topic, format):
    """通過した形式を記録"""
    completed = session.get('completed_formats', {})
    
    if topic not in completed:
        completed[topic] = []
    
    if format not in completed[topic]:
        completed[topic].append(format)
        print(f"✅ 通過記録: {topic} - {format}")
    
    session['completed_formats'] = completed

def get_available_back_buttons(current_topic, current_format):
    """現在の位置に基づいて、戻れるボタンのリストを生成"""
    completed = get_completed_formats(session.get('user_id'))
    buttons = []
    
    if current_topic in completed:
        current_format_index = FORMATS.index(current_format) if current_format in FORMATS else 0
        
        for format in FORMATS[:current_format_index]:
            if format in completed[current_topic]:
                buttons.append({
                    'topic': current_topic,
                    'format': format,
                    'label': f'← {format}に戻る'
                })
    
    current_topic_index = TOPICS.index(current_topic) if current_topic in TOPICS else 0
    
    if current_topic_index > 0:
        prev_topic = TOPICS[current_topic_index - 1]
        
        if prev_topic in completed and '意味説明' in completed[prev_topic]:
            buttons.append({
                'topic': prev_topic,
                'format': '意味説明',
                'label': f'← {prev_topic}に戻る'
            })
    
    return buttons

def login_page():
    return """<!doctype html><html><head><title>SQL学習支援システム - ログイン</title><meta charset="utf-8"><style>body{font-family:Arial,sans-serif;margin:0;padding:0;display:flex;justify-content:center;align-items:center;min-height:100vh;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%)}.login-container{background:white;padding:40px;border-radius:10px;box-shadow:0 10px 25px rgba(0,0,0,0.2);width:100%;max-width:400px}h1{text-align:center;color:#333;margin-bottom:30px}.form-group{margin:20px 0}label{display:block;margin-bottom:8px;color:#555;font-weight:bold}input[type="text"]{width:100%;padding:12px;font-size:16px;border:2px solid #ddd;border-radius:5px;box-sizing:border-box;transition:border-color 0.3s}input[type="text"]:focus{outline:none;border-color:#667eea}input[type="submit"]{width:100%;padding:12px;font-size:18px;background-color:#667eea;color:white;border:none;border-radius:5px;cursor:pointer;transition:background-color 0.3s}input[type="submit"]:hover{background-color:#5568d3}.info{text-align:center;color:#666;font-size:14px;margin-top:20px}</style></head><body><div class="login-container"><h1>SQL学習支援システム</h1><form action='/login' method='post'><div class="form-group"><label for="user_id">ユーザーID:</label><input type="text" id="user_id" name="user_id" required placeholder="例: student001" autofocus></div><input type="submit" value="ログイン"></form><div class="info">※ ユーザーIDを入力してログインしてください</div></div></body></html>"""

def home_page():
    user_id = session.get('user_id', 'ゲスト')
    
    hours, minutes, elapsed_minutes = get_time_display()
    progress_percentage = get_progress_percentage(elapsed_minutes)
    
    test_mode_indicator = ""
    if is_test_mode():
        test_mode_indicator = """
        <div style='background-color:#fff3cd;padding:15px;border-radius:5px;margin:20px 0;border-left:5px solid #ffc107;'>
            <h3>🧪 テストモード ON</h3>
            <p>各形式<strong>2問ずつ</strong>で次の形式に進みます（テスト用）</p>
            <a href='/test_mode' style='color:#856404;text-decoration:underline;'>テストモードをOFFにする</a>
        </div>
        """
    else:
        test_mode_indicator = """
        <div style='text-align:center;margin:20px 0;'>
            <a href='/test_mode' style='color:#667eea;text-decoration:underline;font-size:14px;'>🧪 テストモードをONにする（開発者用）</a>
        </div>
        """
    
    time_display = f"""
    <script>
    setInterval(function() {{
        fetch('/save_session_time', {{method: 'POST'}});
    }}, 5 * 60 * 1000);
    
    window.addEventListener('beforeunload', function() {{
        navigator.sendBeacon('/save_session_time');
    }});
    </script>
    
    <div style='background-color:#e3f2fd;padding:20px;border-radius:10px;margin:20px 0;border-left:5px solid #2196f3;'>
        <h3 style='margin-top:0;'>⏱️ 学習時間</h3>
        <div style='font-size:32px;font-weight:bold;color:#1976d2;margin:10px 0;'>
            {hours}時間 {minutes}分
        </div>
        <div style='background-color:#e0e0e0;border-radius:10px;height:30px;overflow:hidden;margin:15px 0;'>
            <div style='background:linear-gradient(90deg, #4caf50 0%, #8bc34a 100%);height:100%;width:{progress_percentage}%;transition:width 0.3s;display:flex;align-items:center;justify-content:center;color:white;font-weight:bold;'>
                {progress_percentage}%
            </div>
        </div>
        <p style='margin:5px 0;color:#666;font-size:14px;'>
            目標: 8時間（480分） | 残り: {max(0, 480 - elapsed_minutes)}分
        </p>
        <div style='margin-top:15px;'>
            <form action='/reset_timer' method='post' style='display:inline;'>
                <button type='submit' style='background-color:#ff9800;color:white;padding:8px 15px;border:none;border-radius:5px;cursor:pointer;font-size:14px;' onclick='return confirm(\"学習時間をリセットしますか？（学習履歴は保持されます）\")'>
                    ⏱️ 学習時間をリセット
                </button>
            </form>
        </div>
    </div>
    """
    
    time_notice = ""
    if elapsed_minutes >= 60 and elapsed_minutes % 60 < 5:
        time_notice = f"""<div style='background-color:#fff3cd;padding:15px;border-radius:5px;margin:20px 0;border-left:5px solid #ffc107;'>
        <h3>⏰ 休憩のお知らせ</h3>
        <p>学習開始から<strong>{hours}時間{minutes}分</strong>経過しました。</p>
        <p>適度な休憩を取ることをお勧めします！目を休めて、水分補給をしましょう。</p>
        </div>"""
    
    return f"""<!doctype html><html><head><title>SQL学習支援システム</title><meta charset="utf-8"><style>body{{font-family:Arial,sans-serif;margin:20px}}.container{{max-width:700px;margin:0 auto}}.user-info{{background-color:#f0f0f0;padding:15px;border-radius:5px;margin-bottom:20px;display:flex;justify-content:space-between;align-items:center}}.user-name{{font-weight:bold;color:#333}}.logout-button{{background-color:#dc3545;color:white;padding:8px 15px;border:none;border-radius:5px;cursor:pointer;text-decoration:none;font-size:14px}}.logout-button:hover{{background-color:#c82333}}select,input[type="submit"]{{padding:10px;margin:5px;font-size:16px}}.form-group{{margin:15px 0}}.continue-button{{background-color:#28a745;color:white}}.adaptive-section{{background-color:#e3f2fd;padding:20px;border-radius:10px;margin:20px 0;border-left:5px solid #2196f3}}.adaptive-section h3{{margin-top:0;color:#1976d2}}.group-buttons{{display:flex;gap:15px;margin-top:15px}}.group-button{{flex:1;padding:15px;background-color:#fff;border:2px solid #2196f3;border-radius:8px;cursor:pointer;transition:all 0.3s;text-align:center}}.group-button:hover{{background-color:#2196f3;color:white;transform:translateY(-2px);box-shadow:0 4px 8px rgba(0,0,0,0.2)}}.group-button h4{{margin:0 0 10px 0}}.group-button p{{margin:5px 0;font-size:14px;line-height:1.6}}.group-button-link{{text-decoration:none;color:inherit;display:block}}</style></head><body><div class="container"><div class="user-info"><span class="user-name">ログイン中: {user_id}</span><a href="/logout" class="logout-button">ログアウト</a></div><h1>SQL学習支援システム</h1>{test_mode_indicator}{time_display}{time_notice}<div class="adaptive-section"><h3>🎯 適応的学習モード（推奨）</h3><p>意味説明問題を含む4つの形式で学習し、正答率に応じて自動的に形式が変わります。</p><div class="group-buttons"><a href="/select_group?group=A" class="group-button-link"><div class="group-button"><h4>📘 グループA</h4><p>✅ 意味説明あり</p><p>✅ GPTフィードバックあり</p><p>✅ 出題形式動的変化</p></div></a><a href="/select_group?group=B" class="group-button-link"><div class="group-button"><h4>📕 グループB</h4><p>✅ 意味説明あり</p><p>❌ GPTフィードバックなし</p><p>✅ 出題形式動的変化</p><p style="font-size:12px;color:#666;margin-top:8px;">※不正解時は正解例のみ表示</p></div></a></div></div><form action="/history" method="get" style="margin-top:20px;"><input type="submit" value="履歴を見る"></form><form action="/stats" method="get" style="margin-top: 10px;"><input type="submit" value="学習統計を見る" style="background-color: #667eea;"></form><form action="/export_csv" method="get" style="margin-top: 10px;"><input type="submit" value="📥 学習履歴をダウンロード (CSV)" style="background-color: #28a745;"></form></div></body></html>"""

@app.route("/history")
def history():
    if 'user_id' not in session:
        return redirect('/')
    user_id = session['user_id']
    
    try:
        conn = get_db_connection()  # ← 修正
        cursor = conn.cursor()
        
        if DB_TYPE == "postgresql":
            cursor.execute('SELECT * FROM logs WHERE user_id = %s ORDER BY timestamp DESC', (user_id,))
        else:
            cursor.execute('SELECT * FROM logs WHERE user_id = ? ORDER BY timestamp DESC', (user_id,))
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return f"""<h1>学習履歴</h1><p>ユーザー「{user_id}」の学習履歴がありません。</p><br><a href='/home'>ホームに戻る</a>"""
        
        table_html = f"""<style>table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:8px;text-align:left}}th{{background-color:#f2f2f2}}.container{{max-width:1200px;margin:20px auto}}</style><div class="container"><h1>学習履歴（ユーザー: {user_id}）</h1><table><tr><th>ID</th><th>ユーザーID</th><th>日時</th><th>問題ID</th><th>形式</th><th>学習者SQL</th><th>学習者説明</th><th>SQL結果</th><th>SQLフィードバック</th><th>意味結果</th><th>意味フィードバック</th></tr>"""
        
        for row in rows:
            table_html += "<tr>"
            for v in row:
                display_value = str(v)[:100] + "..." if v and len(str(v)) > 100 else str(v)
                table_html += f"<td>{display_value}</td>"
            table_html += "</tr>"
        
        table_html += """</table><br><a href='/home'>ホームに戻る</a></div>"""
        return table_html
    except Exception as e:
        import traceback
        return f"""<h1>学習履歴</h1><p>履歴の読み込み中にエラーが発生しました: {e}</p><pre>{traceback.format_exc()}</pre><br><a href='/home'>ホームに戻る</a>"""

@app.route("/check_sqlite")
def check_sqlite():
    import os
    
    sqlite_file = "学習履歴.db"
    exists = os.path.exists(sqlite_file)
    
    if exists:
        import sqlite3
        conn = sqlite3.connect(sqlite_file)
        cursor = conn.cursor()
        
        # ユーザー別のログ数を確認
        cursor.execute("SELECT user_id, COUNT(*) FROM logs GROUP BY user_id")
        users = cursor.fetchall()
        conn.close()
        
        html = f"""
        <h1>SQLiteファイル発見！</h1>
        <p>ファイルパス: {sqlite_file}</p>
        <h2>ユーザー別のログ数:</h2>
        <ul>
        """
        
        for user_id, count in users:
            html += f"<li>{user_id}: {count}件</li>"
        
        html += """
        </ul>
        <p><a href='/migrate_sqlite_to_postgres'>⚠️ PostgreSQLに移行する</a></p>
        <br><a href='/home'>ホームに戻る</a>
        """
        
        return html
    else:
        return f"""
        <h1>❌ SQLiteファイルが見つかりません</h1>
        <p>ファイルパス: {sqlite_file}</p>
        <p>Renderの再起動によりファイルが削除された可能性があります。</p>
        <br><a href='/home'>ホームに戻る</a>
        """

@app.route("/stats")
def stats():
    if 'user_id' not in session:
        return redirect('/')
    
    user_id = session['user_id']
    stats_data = get_user_statistics(user_id)
    detailed_stats = get_detailed_statistics(user_id)
    
    if not stats_data:
        return f"""<h1>学習統計</h1><p>ユーザー「{user_id}」の学習データがありません。</p><br><a href='/home'>ホームに戻る</a>"""
    
    recent_html = ""
    for log in stats_data['recent_logs']:
        timestamp, problem_id, sql_result, meaning_result = log
        result = sql_result if sql_result else meaning_result
        recent_html += f"<tr><td>{timestamp}</td><td>{problem_id}</td><td>{result}</td></tr>"
    
    detailed_html = ""
    topic_names = {
        'SELECT': 'SELECT句',
        'WHERE': 'WHERE句',
        'ORDERBY': 'ORDER BY句',
        '集約関数': '集約関数',
        'GROUPBY': 'GROUP BY句',
        'HAVING': 'HAVING句',
        'JOIN': 'JOIN句',
        'サブクエリ': 'サブクエリ'
    }
    
    for topic in TOPICS:
        if topic in detailed_stats and any(detailed_stats[topic][f]['total'] > 0 for f in ['選択式', '穴埋め式', '記述式', '意味説明']):
            detailed_html += f"""
            <details style="margin: 20px 0; border: 1px solid #ddd; border-radius: 5px; padding: 10px;">
                <summary style="cursor: pointer; font-weight: bold; font-size: 18px; padding: 10px;">
                    📊 {topic_names[topic]}
                </summary>
                <table style="margin-top: 10px;">
                    <tr>
                        <th>形式</th>
                        <th>回答数</th>
                        <th>正解数</th>
                        <th>正解率</th>
                    </tr>
            """
            
            for format_name in ['選択式', '穴埋め式', '記述式', '意味説明']:
                stat = detailed_stats[topic][format_name]
                if stat['total'] > 0:
                    detailed_html += f"""
                    <tr>
                        <td>{format_name}</td>
                        <td>{stat['total']}</td>
                        <td>{stat['correct']}</td>
                        <td>{stat['accuracy']}%</td>
                    </tr>
                    """
            
            detailed_html += "</table></details>"
    
    html = f"""<!doctype html><html><head><title>学習統計 - SQL学習支援システム</title><meta charset="utf-8"><style>body{{font-family:Arial,sans-serif;margin:20px;background-color:#f5f5f5}}.container{{max-width:800px;margin:0 auto;background:white;padding:30px;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,0.1)}}h1{{color:#333;border-bottom:3px solid #667eea;padding-bottom:10px}}.stat-box{{background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:white;padding:20px;border-radius:10px;margin:20px 0;text-align:center}}.stat-box h2{{margin:0;font-size:48px}}.stat-box p{{margin:5px 0 0 0;font-size:18px}}.stats-grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin:20px 0}}.stat-card{{background:#f9f9f9;padding:20px;border-radius:8px;border-left:4px solid #667eea}}.stat-card h3{{margin:0 0 10px 0;color:#555;font-size:14px}}.stat-card .number{{font-size:32px;font-weight:bold;color:#333}}table{{width:100%;border-collapse:collapse;margin-top:20px}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #ddd}}th{{background-color:#667eea;color:white}}details summary{{background-color:#f0f0f0;}}details[open] summary{{background-color:#e3f2fd;}}.back-link{{display:inline-block;margin-top:20px;padding:10px 20px;background-color:#667eea;color:white;text-decoration:none;border-radius:5px}}.back-link:hover{{background-color:#5568d3}}</style></head><body><div class="container"><h1>📊 学習統計（ユーザー: {user_id}）</h1><div class="stat-box"><h2>{stats_data['overall_accuracy']}%</h2><p>全体の正解率</p></div><div class="stats-grid"><div class="stat-card"><h3>総回答数</h3><div class="number">{stats_data['total_count']}</div></div><div class="stat-card" style="border-left-color:#28a745;"><h3>正解数</h3><div class="number" style="color:#28a745;">{stats_data['correct_count']}</div></div><div class="stat-card" style="border-left-color:#ffc107;"><h3>部分正解数</h3><div class="number" style="color:#ffc107;">{stats_data['partial_count']}</div></div><div class="stat-card" style="border-left-color:#dc3545;"><h3>不正解数</h3><div class="number" style="color:#dc3545;">{stats_data['incorrect_count']}</div></div></div><h2>📈 構文別・形式別の正解率</h2>{detailed_html}<h2>📝 最近の学習履歴（10件）</h2><table><tr><th>日時</th><th>問題ID</th><th>結果</th></tr>{recent_html}</table><a href="/home" class="back-link">ホームに戻る</a></div></body></html>"""
    return html

@app.route("/export_csv")
def export_csv():
    """学習履歴をCSV形式でエクスポート"""
    if 'user_id' not in session:
        return redirect('/')
    
    import csv
    from io import StringIO
    from flask import Response
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM logs ORDER BY timestamp DESC')
        rows = cursor.fetchall()
        
        # カラム名を取得
        if DB_TYPE == "postgresql":
            columns = [desc[0] for desc in cursor.description]
        else:
            columns = [description[0] for description in cursor.description]
        
        conn.close()
        
        # CSV作成
        si = StringIO()
        writer = csv.writer(si)
        writer.writerow(columns)
        writer.writerows(rows)
        
        output = si.getvalue()
        
        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment;filename=learning_history.csv"}
        )
    except Exception as e:
        return f"エラー: {e}"

@app.route("/")
def home():
    if 'user_id' not in session:
        return login_page()
    return redirect('/home')

@app.route("/home")
def home_route():
    if 'user_id' not in session:
        return redirect('/')
    return home_page()

@app.route("/login", methods=["POST"])
def login():
    user_id = request.form.get("user_id", "").strip()
    if not user_id:
        return """<h1>エラー</h1><p>ユーザーIDを入力してください。</p><br><a href='/'>ログイン画面に戻る</a>"""
    session['user_id'] = user_id
    print(f"✅ ログイン成功: {user_id}")
    return redirect('/home')

@app.route("/save_session_time", methods=["POST"])
def save_session_time():
    """現在のセッション時間を累積に保存"""
    if 'user_id' in session:
        end_current_session()
    return "", 204

@app.route("/logout")
def logout():
    user_id = session.get('user_id', 'Unknown')
    end_current_session()
    session.clear()
    print(f"✅ ログアウト: {user_id}")
    return redirect('/')

@app.route("/reset_timer", methods=["POST"])
def reset_timer():
    """学習時間をリセット"""
    if 'user_id' in session:
        session['learning_date'] = None
        session['accumulated_minutes'] = 0
        session['current_session_start'] = None
        print(f"⏱️ 学習時間リセット: {session.get('user_id')}")
    return redirect('/home')

@app.route("/test_mode")
def test_mode():
    """テストモードのON/OFF切り替え"""
    if 'user_id' not in session:
        return redirect('/')
    
    current_mode = session.get('test_mode', False)
    session['test_mode'] = not current_mode
    
    new_status = "ON ✅" if session['test_mode'] else "OFF ❌"
    threshold = "2問ずつ" if session['test_mode'] else "5問ずつ"
    
    return f"""
    <!doctype html>
    <html>
    <head>
        <title>テストモード設定</title>
        <meta charset="utf-8">
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 20px;
                background-color: #f5f5f5;
            }}
            .container {{
                max-width: 600px;
                margin: 50px auto;
                background: white;
                padding: 40px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                text-align: center;
            }}
            h1 {{
                color: #333;
            }}
            .status {{
                font-size: 48px;
                margin: 20px 0;
                font-weight: bold;
                color: {'#28a745' if session['test_mode'] else '#dc3545'};
            }}
            p {{
                font-size: 18px;
                color: #666;
                line-height: 1.6;
            }}
            .info-box {{
                background-color: {'#fff3cd' if session['test_mode'] else '#e3f2fd'};
                padding: 20px;
                border-radius: 5px;
                margin: 20px 0;
                border-left: 5px solid {'#ffc107' if session['test_mode'] else '#2196f3'};
            }}
            .back-link {{
                display: inline-block;
                margin-top: 20px;
                padding: 12px 30px;
                background-color: #667eea;
                color: white;
                text-decoration: none;
                border-radius: 5px;
                font-size: 16px;
            }}
            .back-link:hover {{
                background-color: #5568d3;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🧪 テストモード</h1>
            <div class="status">{new_status}</div>
            <div class="info-box">
                <p><strong>現在の設定:</strong></p>
                <p>各形式を<strong>{threshold}</strong>で次の形式に切り替わります。</p>
                {'<p>⚠️ <strong>テストモード</strong>では、各形式2問で素早く全体をテストできます。</p>' if session['test_mode'] else '<p>通常モードでは、各形式5問で学習進捗を判定します。</p>'}
            </div>
            <a href="/home" class="back-link">ホームに戻る</a>
        </div>
    </body>
    </html>
    """

@app.route("/debug_session")
def debug_session_route():
    session_data = dict(session)
    html = "<h1>セッション情報</h1><pre>"
    for key, value in session_data.items():
        if key == "current_problem":
            html += f"{key}: 問題ID={value.get('id', 'Unknown')}\n"
        else:
            html += f"{key}: {value}\n"
    html += "</pre><br><a href='/home'>ホームに戻る</a>"
    return html

@app.route("/topic_explanation")
def topic_explanation():
    if 'user_id' not in session:
        return redirect('/')
    
    topic = request.args.get('topic', 'SELECT')
    explanation_html = TOPIC_EXPLANATIONS.get(topic, '<p>説明が見つかりません。</p>')
    
    topic_names = {
        'SELECT': 'SELECT句',
        'WHERE': 'WHERE句',
        'ORDERBY': 'ORDER BY句',
        '集約関数': '集約関数',
        'GROUPBY': 'GROUP BY句',
        'HAVING': 'HAVING句',
        'JOIN': 'JOIN句',
        'サブクエリ': 'サブクエリ'
    }
    
    topic_name = topic_names.get(topic, topic)
    
    html = f"""<!doctype html>
<html>
<head>
    <title>{topic_name}の説明 - SQL学習支援システム</title>
    <meta charset="utf-8">
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
            background: white;
            padding: 40px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        h2 {{
            color: #667eea;
            border-bottom: 3px solid #667eea;
            padding-bottom: 10px;
        }}
        h3 {{
            color: #555;
            margin-top: 25px;
        }}
        pre {{
            background-color: #f4f4f4;
            padding: 15px;
            border-left: 4px solid #667eea;
            overflow-x: auto;
            border-radius: 5px;
        }}
        code {{
            background-color: #f4f4f4;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: monospace;
        }}
        ul {{
            line-height: 1.8;
        }}
        .back-link {{
            display: inline-block;
            margin-top: 20px;
            margin-right: 10px;
            padding: 10px 20px;
            background-color: #667eea;
            color: white;
            text-decoration: none;
            border-radius: 5px;
        }}
        .back-link:hover {{
            background-color: #5568d3;
        }}
    </style>
</head>
<body>
    <div class="container">
        {explanation_html}
        
        <div style="margin-top: 20px;">
            <a href="/practice?mode=adaptive&skip_explanation=1" class="back-link">← 学習に戻る</a>
        </div>
    </div>
</body>
</html>"""
    return html

HTML_TEMPLATE = """<!doctype html><html><head><title>SQL学習支援システム</title><meta charset="utf-8"><style>body{font-family:Arial,sans-serif;margin:20px}.container{max-width:800px;margin:0 auto}.back-buttons{margin:10px 0;padding:10px;background-color:#f0f0f0;border-radius:5px}.back-buttons button{padding:8px 15px;margin:5px;background-color:#6c757d;color:white;border:none;border-radius:5px;cursor:pointer;font-size:14px}.back-buttons button:hover{background-color:#5a6268}.return-button{background-color:#28a745 !important;margin-left:15px}.return-button:hover{background-color:#218838 !important}.adaptive-info{background-color:#e3f2fd;padding:10px;border-radius:5px;margin:10px 0}.adaptive-info-b{background-color:#ffe3e3;padding:10px;border-radius:5px;margin:10px 0}.time-notice{background-color:#fff3cd;padding:10px;border-radius:5px;margin:10px 0;border-left:5px solid #ffc107}.topic-link{display:inline-block;margin:10px 0;padding:8px 15px;background-color:#17a2b8;color:white;text-decoration:none;border-radius:5px;font-size:14px}.topic-link:hover{background-color:#138496}textarea{width:100%;padding:10px;font-size:14px}input[type="submit"],button{padding:10px 20px;font-size:16px}.result{background-color:#f9f9f9;padding:15px;border-left:4px solid #007cba;margin:15px 0}.result-correct{background-color:#e8f5e9;border-left:4px solid #4caf50}.result-incorrect{background-color:#ffebee;border-left:4px solid #f44336}pre{background-color:#f4f4f4;padding:10px;overflow-x:auto}.problem-section{margin:20px 0}.blank-template{background-color:#f0f8ff;padding:15px;border:1px solid #ccc;margin:10px 0}</style></head><body><div class="container"><h1><a href="/home" style="text-decoration:none;color:inherit" title="トップページに戻る">SQL学習支援システム</a></h1>{% if time_elapsed >= 60 %}<div class="time-notice">⏰ 学習開始から<strong>{{ time_elapsed }}分</strong>経過しました。適度な休憩をお勧めします！</div>{% endif %}<div><a href="/topic_explanation?topic={{ current_topic }}" class="topic-link">📖 {{ current_topic }}の説明を見る</a></div>{% if back_buttons %}<div class="back-buttons"><strong>📚 復習:</strong>{% for btn in back_buttons %}<form method="get" action="/practice" style="display:inline;"><input type="hidden" name="back_to_topic" value="{{ btn.topic }}"><input type="hidden" name="back_to_format" value="{{ btn.format }}"><button type="submit">{{ btn.label }}</button></form>{% endfor %}{% if is_reviewing %}<form method="get" action="/practice" style="display:inline;"><input type="hidden" name="return_to_main" value="1"><button type="submit" class="return-button">元の学習に戻る</button></form>{% endif %}</div>{% endif %}{% if mode == "adaptive" %}{% if enable_gpt_feedback %}<div class="adaptive-info">📘 <strong>グループA: 適応的学習モード</strong> | 現在: <strong>{{ current_topic }} - {{ current_format }}</strong> | GPTフィードバックあり</div>{% else %}<div class="adaptive-info-b">📕 <strong>グループB: 適応的学習モード</strong> | 現在: <strong>{{ current_topic }} - {{ current_format }}</strong> | GPTフィードバックなし（正解例のみ表示）</div>{% endif %}{% endif %}<form method="post"><input type="hidden" name="format" value="{{ current_format }}"><input type="hidden" name="mode" value="{{ mode }}"><div class="problem-section"><h3>問題 {{ problem.id }}: {{ current_format }}</h3>{% if current_format != "意味説明" %}<p><strong>問題:</strong> {{ problem.title }}</p>{% endif %}{% if current_format=="選択式" %}{% for choice in problem.choices %}{% if choice %}<label><input type="radio" name="student_sql" value="{{ choice }}"> {{ choice }}</label><br>{% endif %}{% endfor %}{% elif current_format=="穴埋め式" %}{% if problem.blank_template %}<div class="blank-template"><strong>穴埋め問題:</strong><br>{{ problem.blank_template }}</div><p><strong>{___} の部分に入る内容を入力してください:</strong></p><textarea name="student_sql" rows="2" cols="60" placeholder="穴埋め部分に入る内容を入力">{{ request.form.student_sql or "" }}</textarea>{% else %}<p>穴埋め問題のテンプレートが設定されていません。</p><textarea name="student_sql" rows="5" cols="80" placeholder="SQL文を入力">{{ request.form.student_sql or "" }}</textarea>{% endif %}{% elif current_format=="記述式" %}<textarea name="student_sql" rows="8" cols="80" placeholder="SQL文を入力してください">{{ request.form.student_sql or "" }}</textarea>{% elif current_format=="意味説明" %}<p><strong>以下のSQL文の意味を日本語で説明してください:</strong></p><pre>{{ problem.answer_sql }}</pre><textarea name="student_explanation" rows="6" cols="80" placeholder="SQL文の意味を日本語で詳しく説明してください">{{ request.form.student_explanation or "" }}</textarea>{% endif %}<br><br><input type="submit" value="評価する"></div></form>{% if result %}<div class="result {% if '正解' in (sql_result or exp_result) %}result-correct{% else %}result-incorrect{% endif %}"><h2>評価結果</h2>{% if current_format=="意味説明" %}<p><strong>結果:</strong> {{ exp_result }}</p>{% if enable_gpt_feedback and exp_feedback %}<p><strong>フィードバック:</strong></p><pre>{{ exp_feedback }}</pre>{% endif %}{% if not enable_gpt_feedback and '不正解' in exp_result and problem.explanation %}<p><strong>正解の説明:</strong></p><pre>{{ problem.explanation }}</pre>{% endif %}{% if enable_gpt_feedback and problem.explanation %}<p><strong>参考: 正解の説明</strong></p><pre>{{ problem.explanation }}</pre>{% endif %}{% else %}<p><strong>SQL評価:</strong> {{ sql_result }}</p>{% if enable_gpt_feedback and sql_feedback %}<p><strong>フィードバック:</strong></p><pre>{{ sql_feedback }}</pre>{% endif %}{% if not enable_gpt_feedback and '不正解' in sql_result and problem.answer_sql %}<p><strong>正解のSQL:</strong></p><pre>{{ problem.answer_sql }}</pre>{% endif %}{% if enable_gpt_feedback and problem.answer_sql %}<p><strong>参考: 正解のSQL</strong></p><pre>{{ problem.answer_sql }}</pre>{% endif %}{% endif %}<form method="get" action="/practice"><input type="hidden" name="format" value="{{ current_format }}"><input type="hidden" name="mode" value="{{ mode }}"><input type="hidden" name="next" value="1"><button type="submit">次の問題に進む</button></form></div>{% endif %}</div></body></html>"""

@app.route("/practice", methods=["GET", "POST"])
def practice():
    if 'user_id' not in session:
        return redirect('/')
        # ★★★ デバッグ情報を追加 ★★★
        print("=" * 50)
        print("🔍 practice関数開始")
        print(f"   method: {request.method}")
        print(f"   args: {dict(request.args)}")
        print(f"   session['learning_progress']: {session.get('learning_progress')}")
        print(f"   session['topic_explained']: {session.get('topic_explained')}")
        print(f"   session.get('current_problem'): {session.get('current_problem', {}).get('id', 'None')}")
        print("=" * 50)
    
    time_elapsed = get_time_elapsed()
    
    all_problems = []
    for sheet in ["Sheet1", "Sheet2", "Sheet3", "Sheet4", "Sheet5", "Sheet6", "Sheet7", "Sheet8"]:
        try:
            problems = load_problems(sheet)
            all_problems.extend(problems)
        except Exception as e:
            print(f"シート {sheet} の読み込みエラー: {e}")
    
    if not all_problems:
        return """<h1>エラー</h1><p>問題ファイル (problems.xlsx) が見つからないか、問題が読み込めません。</p><a href='/home'>ホームに戻る</a>"""

    mode = request.args.get("mode", session.get("mode", "random"))
    session["mode"] = mode
    
    # グループA/Bの判定（修正版）
    if mode == "adaptive_b":
        enable_gpt_feedback = False
        mode = "adaptive"
        session['enable_gpt_feedback'] = False  # ← ここで保存
    elif mode == "adaptive_a":
        enable_gpt_feedback = True
        mode = "adaptive"
        session['enable_gpt_feedback'] = True  # ← ここで保存
    else:
        # セッションから取得（既に設定されている場合はそれを使う）
        enable_gpt_feedback = session.get('enable_gpt_feedback', True)
    
    if mode == "adaptive":
        session['enable_gpt_feedback'] = enable_gpt_feedback
    else:
        enable_gpt_feedback = session.get('enable_gpt_feedback', True)
    
    start_topic = request.args.get("start_topic")
    
    if start_topic and mode == "adaptive":
        progress = {
            'current_topic': start_topic,
            'current_format': '選択式',
            'format_question_count': 0,
            'format_start_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        session['learning_progress'] = progress
    
    back_to_topic = request.args.get("back_to_topic")
    back_to_format = request.args.get("back_to_format")
    
    if back_to_topic and back_to_format:
        session.pop('current_problem', None)
        
        topic_prefix_map = {
            'SELECT': 'SELECT_',
            'WHERE': 'WHERE_',
            'ORDERBY': 'ORDERBY_',
            '集約関数': 'AGG_',
            'GROUPBY': 'GROUPBY_',
            'HAVING': 'HAVING_',
            'JOIN': 'JOIN_',
            'サブクエリ': 'SUBQUERY_'
        }
        
        prefix = topic_prefix_map.get(back_to_topic, 'SELECT_')
        topic_problems = [p for p in all_problems if p['id'].startswith(prefix)]
        
        if topic_problems:
            selected_problem = random.choice(topic_problems)
            session["current_problem"] = selected_problem
            session['temp_format'] = back_to_format
            session['temp_topic'] = back_to_topic
            session['is_reviewing'] = True
            print(f"🔙 復習モード: {back_to_topic} - {back_to_format}")
    
    return_to_main = request.args.get("return_to_main")

    if return_to_main == "1":
        session.pop('temp_format', None)
        session.pop('temp_topic', None)
        session.pop('is_reviewing', None)
        session.pop('current_problem', None)
        print(f"↩️ 元の学習に戻ります")
        
        progress = session.get('learning_progress', {
            'current_topic': 'SELECT',
            'current_format': '選択式'
        })
        current_topic = progress['current_topic']
        current_format = progress['current_format']
        
        topic_prefix_map = {
            'SELECT': 'SELECT_',
            'WHERE': 'WHERE_',
            'ORDERBY': 'ORDERBY_',
            '集約関数': 'AGG_',
            'GROUPBY': 'GROUPBY_',
            'HAVING': 'HAVING_',
            'JOIN': 'JOIN_',
            'サブクエリ': 'SUBQUERY_'
        }
        
        prefix = topic_prefix_map.get(current_topic, 'SELECT_')
        topic_problems = [p for p in all_problems if p['id'].startswith(prefix)]
        
        if topic_problems:
            selected_problem = random.choice(topic_problems)
            session["current_problem"] = selected_problem
            print(f"↩️ 元の進捗に戻りました: {current_topic} - {current_format}")
    
            if mode == "adaptive":
                # skip_explanation パラメータを先にチェック
                skip_explanation = request.args.get('skip_explanation', '0')
                if skip_explanation == '1':
                    session['topic_explained'] = True
                
                if 'temp_format' in session and 'temp_topic' in session:
                    current_topic = session['temp_topic']
                    current_format = session['temp_format']
                else:
                    progress = session.get('learning_progress', {
                        'current_topic': 'SELECT',
                        'current_format': '選択式'
                    })
                    current_topic = progress['current_topic']
                    current_format = progress['current_format']
                
                    # topic_explained がまだで、skip_explanation でもない場合のみリダイレクト
                    if not session.get('topic_explained'):
                        session['topic_explained'] = True
                        return redirect(f'/topic_explanation?topic={current_topic}')
                    
                    print(f"Debug - 適応的出題: Topic={current_topic}, Format={current_format}")
    else:
        current_format = request.args.get("format", FORMATS[0])
    
    result = False
    sql_result = sql_feedback = exp_result = exp_feedback = ""

    if request.method == "POST":
        if "current_problem" not in session:
            if mode == "random":
                session["remaining_problems"] = all_problems.copy()
                random.shuffle(session["remaining_problems"])
                session["current_problem"] = session["remaining_problems"].pop()
            else:
                session["problem_index"] = 0
                session["current_problem"] = all_problems[0]
        
        problem = session["current_problem"]
        user_sql = request.form.get("student_sql", "").strip()
        user_exp = request.form.get("student_explanation", "").strip()
        eval_format = request.form.get("format", current_format)
        
        enable_gpt_feedback = session.get('enable_gpt_feedback', True)

        if eval_format == "意味説明":
            if not user_exp:
                if enable_gpt_feedback:
                    exp_result, exp_feedback = "不正解 ❌", "説明が入力されていません。"
                else:
                    exp_result, exp_feedback = "不正解 ❌", ""
            else:
                exp_result, exp_feedback = evaluate_meaning(user_exp, problem["explanation"], enable_gpt_feedback, problem)
        else:
            if not user_sql:
                if enable_gpt_feedback:
                    sql_result, sql_feedback = "不正解 ❌", "SQL文が入力されていません。"
                else:
                    sql_result, sql_feedback = "不正解 ❌", ""
            else:
                sql_result, sql_feedback = evaluate_sql(user_sql, problem["answer_sql"], eval_format, problem, enable_gpt_feedback)

        user_id = session.get('user_id', 'unknown')
        save_log(user_id, problem["id"], eval_format, user_sql, user_exp, sql_result, sql_feedback, exp_result, exp_feedback)
        
        if not session.get('is_reviewing'):
            problem_topic = extract_topic_from_problem_id(problem["id"])
            add_completed_format(problem_topic, eval_format)

        result = True
    
    else:
        # ★★★ デバッグ：セッション状態を確認 ★★★
        print(f"🔍 practice - GET処理開始")
        print(f"   learning_progress: {session.get('learning_progress')}")
        print(f"   current_problem: {session.get('current_problem', {}).get('id', 'None')}")

        if request.args.get("next") == "1":
            was_reviewing = session.get('is_reviewing', False)
            
            session.pop('temp_format', None)
            session.pop('temp_topic', None)
            session.pop('is_reviewing', None)
            
            if was_reviewing:
                print("📚 復習モードを終了します")
            
            if mode == "adaptive" and "current_problem" in session and not was_reviewing:
                user_id = session.get('user_id', 'unknown')
                last_problem = session["current_problem"]
                topic = extract_topic_from_problem_id(last_problem["id"])
                
                progress = session.get('learning_progress', {
                    'current_topic': 'SELECT',
                    'current_format': '選択式',
                    'format_question_count': 0,
                    'format_start_time': None
                })
                current_format_for_check = progress['current_format']
                start_time = progress.get('format_start_time')
                
                format_question_count = progress.get('format_question_count', 0) + 1
                progress['format_question_count'] = format_question_count
                session['learning_progress'] = progress
                
                threshold = get_format_question_threshold(current_format_for_check)

                accuracy_data = get_recent_accuracy(user_id, topic, current_format_for_check, limit=threshold, start_time=start_time)
                
                print(f"🔍 形式変更の判定: Topic={topic}, Format={current_format_for_check}")
                print(f"   この形式での回答数: {format_question_count}回 (閾値: {threshold}問)")
                print(f"   threshold={threshold}, accuracy_data={accuracy_data}")
                if start_time:
                    print(f"   形式開始時刻: {start_time}")
                if accuracy_data:
                    print(f"   直近の成績: {accuracy_data['correct']}/{accuracy_data['total']}問正解 (正答率: {accuracy_data['accuracy']}%)")
                else:
                    print(f"   まだデータなし")
                
                if format_question_count >= threshold and accuracy_data and accuracy_data['total'] >= threshold:
                    
                    print(f"   → 判定開始")
                    
                    if current_format_for_check == '意味説明':
                        if accuracy_data['accuracy'] >= 70:
                            add_completed_format(topic, '意味説明')
                            
                            current_index = TOPICS.index(topic) if topic in TOPICS else 0
                            if current_index < len(TOPICS) - 1:
                                next_topic = TOPICS[current_index + 1]
                                next_format = '選択式'
                                update_learning_progress(user_id, next_topic, next_format)
                                current_format = next_format
                                
                                session.pop('topic_explained', None)
                                
                                print(f"✅ 次の構文へ: {topic} → {next_topic} (正答率: {accuracy_data['accuracy']}%)")
                            else:
                                print(f"✅ 全ての構文を完了しました！")
                        else:
                            next_format = '記述式'
                            update_learning_progress(user_id, topic, next_format)
                            current_format = next_format
                            print(f"✅ 下位形式へ: {current_format_for_check} → {next_format} (正答率: {accuracy_data['accuracy']}%)")
                    else:
                        next_format = get_next_format(current_format_for_check, accuracy_data['accuracy'])
                        
                        print(f"   → 次の形式候補: {next_format}")
                        
                        if next_format != current_format_for_check:
                            add_completed_format(topic, next_format)
                            
                            update_learning_progress(user_id, topic, next_format)
                            current_format = next_format
                            print(f"✅ 形式変更: {current_format_for_check} → {next_format} (正答率: {accuracy_data['accuracy']}%)")
            
            if mode == "adaptive" and not session.get('topic_explained') and not session.get('is_reviewing'):
                progress = session.get('learning_progress', {})
                current_topic = progress.get('current_topic', 'SELECT')
                return redirect(f'/topic_explanation?topic={current_topic}')
            
            if mode == "adaptive":
                if session.get('is_reviewing'):
                    topic = session.get('temp_topic', 'SELECT')
                    current_format = session.get('temp_format', '選択式')
                else:
                    progress = session.get('learning_progress', {
                        'current_topic': 'SELECT',
                        'current_format': '選択式',
                        'format_question_count': 0,
                        'format_start_time': None
                    })
                    topic = progress['current_topic']
                    current_format = progress['current_format']
                
                print(f"Debug - GET処理: Topic={topic}, Format={current_format}")
                
                topic_prefix_map = {
                    'SELECT': 'SELECT_',
                    'WHERE': 'WHERE_',
                    'ORDER BY': 'ORDERBY_',
                    'ORDERBY': 'ORDERBY_',
                    '集約関数': 'AGG_',
                    'GROUP BY': 'GROUPBY_',
                    'GROUPBY': 'GROUPBY_',
                    'HAVING': 'HAVING_',
                    'JOIN': 'JOIN_',
                    'サブクエリ': 'SUBQUERY_'
                    }
                
                prefix = topic_prefix_map.get(topic, 'SELECT_')
                topic_problems = [p for p in all_problems if p['id'].startswith(prefix)]
                
                if topic_problems:
                    recent_problem_ids = session.get('recent_problem_ids', {})
                    recent_ids_for_topic = recent_problem_ids.get(topic, [])
                    
                    available_problems = [p for p in topic_problems if p['id'] not in recent_ids_for_topic]
                    
                    if not available_problems:
                        print(f"   📚 全ての問題を出題済み。履歴をリセットします。")
                        recent_ids_for_topic = []
                        available_problems = topic_problems.copy()
                    
                    selected_problem = random.choice(available_problems)
                    session["current_problem"] = selected_problem
                    
                    recent_ids_for_topic.append(selected_problem['id'])
                    if len(recent_ids_for_topic) > 15:
                        recent_ids_for_topic.pop(0)
                    
                    recent_problem_ids[topic] = recent_ids_for_topic
                    session['recent_problem_ids'] = recent_problem_ids
                    
                    print(f"Debug - 次の問題: {selected_problem['id']}")
                    print(f"Debug - 直近15問: {recent_ids_for_topic}")
                else:
                    session["current_problem"] = random.choice(all_problems)
                    print(f"⚠️  {prefix} の問題が見つかりません")
                    
            elif mode == "random":
                if "remaining_problems" not in session or not session["remaining_problems"]:
                    session["remaining_problems"] = all_problems.copy()
                    random.shuffle(session["remaining_problems"])
                    if "current_problem" in session:
                        current_id = session["current_problem"]["id"]
                        session["remaining_problems"] = [p for p in session["remaining_problems"] if p["id"] != current_id]
                if session["remaining_problems"]:
                    session["current_problem"] = session["remaining_problems"].pop()
                else:
                    session["remaining_problems"] = all_problems.copy()
                    random.shuffle(session["remaining_problems"])
                    session["current_problem"] = session["remaining_problems"].pop()
            else:
                idx = session.get("problem_index", 0)
                session["current_problem"] = all_problems[idx % len(all_problems)]
                session["problem_index"] = idx + 1
        
        # ★★★ ここを修正：elif → if に変更 ★★★
        if "current_problem" not in session:
            session["last_format"] = current_format
            
            if mode == "adaptive":
                # 既にprogressがあればそれを使う
                progress = session.get('learning_progress', {
                    'current_topic': 'SELECT',
                    'current_format': '選択式',
                    'format_question_count': 0,
                    'format_start_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
                
                # progressを上書きしない
                if 'learning_progress' not in session:
                    session['learning_progress'] = progress
                
                current_topic = progress['current_topic']
                current_format = progress['current_format']
                
                if not session.get('topic_explained'):
                    return redirect(f'/topic_explanation?topic={current_topic}')
                
                # current_topicに応じた問題を取得
                topic_prefix_map = {
                    'SELECT': 'SELECT_',
                    'WHERE': 'WHERE_',
                    'ORDERBY': 'ORDERBY_',
                    '集約関数': 'AGG_',
                    'GROUPBY': 'GROUPBY_',
                    'HAVING': 'HAVING_',
                    'JOIN': 'JOIN_',
                    'サブクエリ': 'SUBQUERY_'
                }
                
                prefix = topic_prefix_map.get(current_topic, 'SELECT_')
                topic_problems = [p for p in all_problems if p['id'].startswith(prefix)]
                
                if topic_problems:
                    selected_problem = random.choice(topic_problems)
                    session["current_problem"] = selected_problem
                    
                    add_completed_format(current_topic, current_format)
                    
                    recent_problem_ids = {current_topic: [selected_problem['id']]}
                    session['recent_problem_ids'] = recent_problem_ids
                    
                    print(f"Debug - 初回問題（ジャンプ後）: {selected_problem['id']}, Topic={current_topic}, Format={current_format}")
                else:
                    session["current_problem"] = all_problems[0]
            elif mode == "random":
                session["remaining_problems"] = all_problems.copy()
                random.shuffle(session["remaining_problems"])
                session["current_problem"] = session["remaining_problems"].pop()
            else:
                session["problem_index"] = 1
                session["current_problem"] = all_problems[0]
        
        # ★★★ ここを修正：elif → if に変更 ★★★
        if request.args.get("format") and session.get("last_format") != current_format:
            session["last_format"] = current_format

    problem = session.get("current_problem")
    if not problem:
        problem = all_problems[0]
        session["current_problem"] = problem
    
    if 'temp_format' in session and 'temp_topic' in session:
        current_topic = session['temp_topic']
        current_format = session['temp_format']
    else:
        if mode == "adaptive":
            progress = session.get('learning_progress', {
                'current_topic': 'SELECT',
                'current_format': '選択式'
            })
            current_topic = progress['current_topic']
            current_format = progress['current_format']
        else:
            current_topic = extract_topic_from_problem_id(problem['id'])
    
    back_buttons = get_available_back_buttons(current_topic, current_format)
    
    is_reviewing = session.get('is_reviewing', False)

    return render_template_string(HTML_TEMPLATE, problem=problem, formats=FORMATS, current_format=current_format, current_topic=current_topic, result=result, sql_result=sql_result, sql_feedback=sql_feedback, exp_result=exp_result, exp_feedback=exp_feedback, mode=mode, request=request, time_elapsed=time_elapsed, enable_gpt_feedback=enable_gpt_feedback, back_buttons=back_buttons, is_reviewing=is_reviewing)

@app.route("/select_group")
def select_group():
    if 'user_id' not in session:
        return redirect('/')
    
    group = request.args.get('group', 'A')
    
    # グループ設定を保存
    if group == 'B':
        session['enable_gpt_feedback'] = False
    else:
        session['enable_gpt_feedback'] = True
    
    session['mode'] = 'adaptive'
    
    group_name = "グループA" if group == "A" else "グループB"
    group_desc = "GPTフィードバックあり" if group == "A" else "GPTフィードバックなし（正解例のみ表示）"
    
    # 学習位置選択ボタンを生成
    jump_buttons = ""
    topics = ['SELECT', 'WHERE', 'ORDERBY', '集約関数', 'GROUPBY', 'HAVING', 'JOIN', 'サブクエリ']
    formats = ['選択式', '穴埋め式', '記述式', '意味説明']
    
    topic_names = {
        'SELECT': 'SELECT句',
        'WHERE': 'WHERE句',
        'ORDERBY': 'ORDER BY句',
        '集約関数': '集約関数',
        'GROUPBY': 'GROUP BY句',
        'HAVING': 'HAVING句',
        'JOIN': 'JOIN句',
        'サブクエリ': 'サブクエリ'
    }
    
    for topic in topics:
        jump_buttons += f"<div style='margin-bottom:20px;'><h4>{topic_names.get(topic, topic)}</h4><div style='display:flex;gap:10px;flex-wrap:wrap;'>"
        for format in formats:
            jump_buttons += f"""
            <a href='/jump_to?topic={topic}&format={format}' style='text-decoration:none;'>
                <button style='padding:10px 20px;background:#667eea;color:white;border:none;border-radius:5px;cursor:pointer;'>
                    {format}
                </button>
            </a>
            """
        jump_buttons += "</div></div>"
    
    html = f"""<!doctype html>
<html>
<head>
    <title>学習位置を選択 - SQL学習支援システム</title>
    <meta charset="utf-8">
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            max-width: 900px;
            margin: 0 auto;
            background: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        .group-info {{
            background-color: {'#e3f2fd' if group == 'A' else '#ffe3e3'};
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 30px;
            border-left: 5px solid {'#2196f3' if group == 'A' else '#f44336'};
        }}
        .start-button {{
            background-color: #28a745;
            color: white;
            padding: 15px 30px;
            border: none;
            border-radius: 8px;
            font-size: 18px;
            cursor:pointer;
            text-decoration: none;
            display: inline-block;
            margin-bottom: 30px;
        }}
        .start-button:hover {{
            background-color: #218838;
        }}
        h4 {{
            color: #667eea;
            margin-top: 0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📍 学習位置を選択</h1>
        
        <div class="group-info">
            <h3>選択中: {group_name}</h3>
            <p>{group_desc}</p>
        </div>
        
        <div style="background-color:#fff3cd;padding:15px;border-radius:5px;margin-bottom:30px;border-left:5px solid #ffc107;">
            <h3>💡 学習位置の選択について</h3>
            <p><strong>初めての方:</strong> 「最初から始める」をクリックしてください</p>
            <p><strong>システムトラブルで履歴がリセットされた方:</strong> 以前学習していた位置を選択してください</p>
        </div>
        
        <a href="/practice?mode=adaptive" class="start-button">
            🚀 最初から始める（SELECT - 選択式）
        </a>
        
        <h2>または、途中から再開する:</h2>
        
        {jump_buttons}
        
        <div style="margin-top:30px;">
            <a href="/home" style="color:#667eea;text-decoration:none;">← ホームに戻る</a>
        </div>
    </div>
</body>
</html>"""
    
    return html

@app.route("/jump_to")
def jump_to():
    if 'user_id' not in session:
        return redirect('/')
    
    topic = request.args.get('topic', 'SELECT')
    format = request.args.get('format', '選択式')
    
    # ★★★ 修正：古いセッションデータをクリア ★★★
    session.pop('learning_progress', None)
    session.pop('current_problem', None)
    session.pop('recent_problem_ids', None)
    session.pop('completed_formats', None)
    session.pop('topic_explained', None)
    
    # 学習進捗を新しく設定
    progress = {
        'current_topic': topic,
        'current_format': format,
        'format_question_count': 0,
        'format_start_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    session['learning_progress'] = progress
    session['topic_explained'] = True  # 説明ページをスキップ
    
    print(f"🚀 ジャンプ機能: {topic} - {format} にジャンプしました")
    print(f"   設定した進捗: {progress}")
    
    # 直接 practice に飛ぶ
    return redirect('/practice?mode=adaptive')

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    if os.environ.get("ENVIRONMENT") == "production":
        app.run(host='0.0.0.0', port=port)
    else:
        app.run(debug=True, port=port)


















