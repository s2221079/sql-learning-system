from flask import Flask, request, render_template_string, redirect, url_for, session
from openai import OpenAI
import os
import sqlite3
from datetime import datetime
import re
import random
import openpyxl

app = Flask(__name__)
app.secret_key = "s2221079"

# OpenAIクライアントの初期化
api_key = os.environ.get("OPENAI_API_KEY")
if api_key:
    client = OpenAI(api_key=api_key)
else:
    client = None
    print("⚠️ OPENAI_API_KEY が設定されていません")
    
DB_FILE = "学習履歴.db"

# データベース初期化
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
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
    
    cursor.execute("PRAGMA table_info(logs)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'format' not in columns:
        cursor.execute('ALTER TABLE logs ADD COLUMN format TEXT')
        print("✅ format列を追加しました")
    
    conn.commit()
    conn.close()

init_db()

FORMATS = ["選択式", "穴埋め式", "記述式", "意味説明"]

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

def evaluate_sql(user_sql, correct_sql, format, problem=None):
    user_sql = user_sql.lower().strip().rstrip(";")
    correct_sql = correct_sql.lower().strip().rstrip(";")

    if format == "穴埋め式" and problem and problem.get("blank_template") and problem.get("blank_answer"):
        user_answer = re.sub(r'\s+', '', user_sql.lower().strip())
        correct_answer = re.sub(r'\s+', '', problem["blank_answer"].lower().strip())
        if user_answer == correct_answer:
            return "正解 ✅", "完璧です！"
        else:
            return "不正解 ❌", f"正解は「{problem['blank_answer']}」です。"
    
    if format == "選択式":
        if user_sql == correct_sql:
            return "正解 ✅", "完璧なSQL文です！"
        else:
            return "不正解 ❌", "SQL文が正しくありません。"
    
    if format == "記述式":
        user_sql_normalized = normalize_sql_strict(user_sql)
        correct_sql_normalized = normalize_sql_strict(correct_sql)
        
        if user_sql_normalized == correct_sql_normalized:
            return "正解 ✅", "完璧なSQL文です！"
        
        if 'where' in correct_sql_normalized and 'where' not in user_sql_normalized:
            return "不正解 ❌", "WHERE句が欠けています。条件を指定するには WHERE を使用してください。"
        
        if 'from' not in user_sql_normalized:
            return "不正解 ❌", "FROM句が欠けています。テーブル名を指定してください。"
        
        if not user_sql_normalized.startswith('select'):
            return "不正解 ❌", "SQL文はSELECTから始まる必要があります。"
        
        try:
            if os.environ.get("OPENAI_API_KEY"):
                prompt = f"""あなたはSQL学習システムの評価者です。学習者が書いたSQL文を以下の基準で厳密に評価してください。

【評価基準】
■ 不正解 ❌（以下のいずれかに該当）
1. SQL構文エラー（キーワードのスペルミス、文法違反など）
2. SQL文として成立していない
3. 必須の句（WHERE, FROM等）が欠けている

■ 部分正解 ⚠️（以下のいずれかに該当）
1. 構文は完全に正しいが、存在しない列名を指定している
2. 構文は完全に正しいが、列の記述順序が正解例と異なる
3. 構文は完全に正しいが、テーブル名や列名の大文字小文字が正解例と異なる

■ 正解 ✅（以下の全てに該当）
1. SQL構文が完全に正しい
2. 列名とテーブル名が正解例と完全に一致（大文字小文字・順序も含む）
3. 実際に実行できるSQL文である

正解例: {correct_sql_normalized}
学習者のSQL: {user_sql_normalized}

回答形式:
判定結果: 正解/部分正解/不正解
フィードバック: （具体的なアドバイス）"""
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    temperature=0,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=200
                )
                text = response.choices[0].message.content.strip()
                
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
    return "不正解 ❌", "SQL文が正しくありません。"

def evaluate_meaning(user_explanation, correct_explanation):
    if not user_explanation.strip():
        return "不正解 ❌", "説明が入力されていません。"
    
    user_explanation = user_explanation.strip()
    
    try:
        if os.environ.get("OPENAI_API_KEY"):
            prompt = f"""あなたはSQL学習システムの評価者です。学習者のSQL説明を以下の基準で評価してください。

【評価基準】
■ 不正解 ❌（以下のいずれかに該当）
1. SQL文の意味を根本的に誤解している
2. 重要な要素が大部分欠けている（50%以上の情報が不足）
3. テーブル名や列名が明らかに間違っている

■ 部分正解 ⚠️（以下のいずれかに該当）
1. 基本的な意味は正しいが、重要な要素が一部欠けている（20-50%の情報が不足）
2. やや不正確な表現があるが、概ね理解できている

■ 正解 ✅（以下の全てに該当）
1. SQL文の動作を正確に理解している
2. 重要な要素（テーブル名、列名、条件など）が全て含まれている
3. 意味的に正しい説明になっている

参考となる正解例: {correct_explanation}
学習者の説明: {user_explanation}

回答形式:
判定結果: 正解/部分正解/不正解
フィードバック: （具体的なアドバイス）"""
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200
            )
            text = response.choices[0].message.content.strip()
            result_match = re.search(r"判定結果[:：]\s*(正解|部分正解|不正解)", text)
            feedback_match = re.search(r"フィードバック[:：]\s*(.*)", text, re.DOTALL)
            result = result_match.group(1) if result_match else "不正解"
            feedback = feedback_match.group(1).strip() if feedback_match else "説明が不十分です。"
            if result == "正解":
                result = "正解 ✅"
            elif result == "部分正解":
                result = "部分正解 ⚠️"
            else:
                result = "不正解 ❌"
            return result, feedback
    except Exception as e:
        print(f"OpenAI API エラー: {e}")
    
    return "不正解 ❌", "説明が不十分です。"

def save_log(user_id, problem_id, format, user_sql, user_explanation, sql_result, sql_feedback, exp_result, exp_feedback):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO logs (user_id, timestamp, problem_id, format, user_sql, user_explanation, 
                            sql_result, sql_feedback, meaning_result, meaning_feedback)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, timestamp, problem_id, format, user_sql, user_explanation, 
              sql_result, sql_feedback, exp_result, exp_feedback))
        conn.commit()
        conn.close()
        print(f"✅ ログ書き込み成功: {timestamp} (User: {user_id}, Format: {format})")
    except Exception as e:
        print("❌ ログ書き込み失敗:", e)

def get_user_statistics(user_id):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM logs WHERE user_id = ?', (user_id,))
        total_count = cursor.fetchone()[0]
        
        if total_count == 0:
            conn.close()
            return None
        
        cursor.execute('''
            SELECT COUNT(*) FROM logs 
            WHERE user_id = ? 
            AND (sql_result = '正解 ✅' OR meaning_result = '正解 ✅')
        ''', (user_id,))
        correct_count = cursor.fetchone()[0]
        
        cursor.execute('''
            SELECT COUNT(*) FROM logs 
            WHERE user_id = ? 
            AND (sql_result = '部分正解 ⚠️' OR meaning_result = '部分正解 ⚠️')
        ''', (user_id,))
        partial_count = cursor.fetchone()[0]
        
        cursor.execute('''
            SELECT COUNT(*) FROM logs 
            WHERE user_id = ? 
            AND (sql_result = '不正解 ❌' OR meaning_result = '不正解 ❌')
        ''', (user_id,))
        incorrect_count = cursor.fetchone()[0]
        
        overall_accuracy = (correct_count / total_count * 100) if total_count > 0 else 0
        
        format_stats = {}
        for format_name in ['選択式', '穴埋め式', '記述式', '意味説明']:
            cursor.execute('''
                SELECT COUNT(*) FROM logs 
                WHERE user_id = ? AND format = ?
            ''', (user_id, format_name))
            format_total = cursor.fetchone()[0]
            
            if format_total > 0:
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
        return None

def get_detailed_statistics(user_id):
    """構文別・形式別の詳細統計を取得"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # 【修正】構文リストを更新
        topics = ['SELECT', 'WHERE', 'ORDERBY', '集約関数', 'GROUPBY', 'HAVING', 'JOIN']
        formats = ['選択式', '穴埋め式', '記述式', '意味説明']
        
        detailed_stats = {}
        
        for topic in topics:
            detailed_stats[topic] = {}
            
            for format_name in formats:
                # その構文・形式での総回答数
                cursor.execute('''
                    SELECT COUNT(*) FROM logs 
                    WHERE user_id = ? AND problem_id LIKE ? AND format = ?
                ''', (user_id, f"{topic}%", format_name))
                total = cursor.fetchone()[0]
                
                if total > 0:
                    # その構文・形式での正解数
                    cursor.execute('''
                        SELECT COUNT(*) FROM logs 
                        WHERE user_id = ? AND problem_id LIKE ? AND format = ?
                        AND (sql_result = '正解 ✅' OR meaning_result = '正解 ✅')
                    ''', (user_id, f"{topic}%", format_name))
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
        return {}

def get_recent_accuracy(user_id, topic, format, limit=5, start_time=None):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        if start_time:
            cursor.execute('''
                SELECT sql_result, meaning_result 
                FROM logs 
                WHERE user_id = ? AND problem_id LIKE ? AND format = ? AND timestamp >= ?
                ORDER BY timestamp DESC 
                LIMIT ?
            ''', (user_id, f"{topic}%", format, start_time, limit))
        else:
            cursor.execute('''
                SELECT sql_result, meaning_result 
                FROM logs 
                WHERE user_id = ? AND problem_id LIKE ? AND format = ?
                ORDER BY timestamp DESC 
                LIMIT ?
            ''', (user_id, f"{topic}%", format, limit))
        
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
    """学習進捗を取得（どの構文のどの形式まで到達したか）"""
    # 【修正】構文リストを更新
    topics = ['SELECT', 'WHERE', 'ORDERBY', '集約関数', 'GROUPBY', 'HAVING', 'JOIN']
    
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
    
    # 【修正】構文リストを更新
    topics = ['SELECT', 'WHERE', 'ORDERBY', '集約関数', 'GROUPBY', 'HAVING', 'JOIN']
    if topic in topics:
        progress['topic_index'] = topics.index(topic)
    
    session['learning_progress'] = progress

def extract_topic_from_problem_id(problem_id):
    if '_' in problem_id:
        return problem_id.split('_')[0]
    return 'SELECT'

def analyze_weak_points(user_id):
    """ユーザーの弱点を分析（正答率が低い構文を特定）"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # 【修正】構文リストを更新
        topics = ['SELECT', 'WHERE', 'ORDERBY', '集約関数', 'GROUPBY', 'HAVING', 'JOIN']
        weak_points = []
        
        for topic in topics:
            # その構文での総回答数
            cursor.execute('''
                SELECT COUNT(*) FROM logs 
                WHERE user_id = ? AND problem_id LIKE ?
            ''', (user_id, f"{topic}%"))
            total = cursor.fetchone()[0]
            
            if total >= 3:  # 3問以上解いている構文のみ分析
                # その構文での正解数
                cursor.execute('''
                    SELECT COUNT(*) FROM logs 
                    WHERE user_id = ? AND problem_id LIKE ?
                    AND (sql_result = '正解 ✅' OR meaning_result = '正解 ✅')
                ''', (user_id, f"{topic}%"))
                correct = cursor.fetchone()[0]
                
                accuracy = (correct / total * 100) if total > 0 else 0
                
                # 正答率が60%未満を弱点とする
                if accuracy < 60:
                    weak_points.append({
                        'topic': topic,
                        'total': total,
                        'correct': correct,
                        'accuracy': round(accuracy, 1)
                    })
        
        conn.close()
        
        # 正答率が低い順にソート
        weak_points.sort(key=lambda x: x['accuracy'])
        
        return weak_points
    except Exception as e:
        print(f"弱点分析エラー: {e}")
        return []

def get_incorrect_problems(user_id, topic, limit=3):
    """特定の構文で間違えた問題を取得"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT DISTINCT problem_id, user_sql, sql_feedback
            FROM logs 
            WHERE user_id = ? AND problem_id LIKE ?
            AND (sql_result = '不正解 ❌' OR meaning_result = '不正解 ❌')
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (user_id, f"{topic}%", limit))
        
        results = cursor.fetchall()
        conn.close()
        
        return results
    except Exception as e:
        print(f"間違えた問題の取得エラー: {e}")
        return []

def generate_similar_problem(topic, reference_problem=None, difficulty='medium'):
    """GPTを使って類似問題を生成"""
    try:
        if not os.environ.get("OPENAI_API_KEY"):
            print("OpenAI APIキーが設定されていません")
            return None
        
        difficulty_desc = {
            'easy': '基本的で簡単な',
            'medium': '標準的な',
            'hard': '応用的で難しい'
        }
        
        table_def = """
employeesテーブル:
- id (整数, 主キー)
- name (文字列, 従業員名)
- salary (整数, 給与)
- department_id (整数, 部署ID)
"""
        
        # 【修正】構文ごとに詳細な指示を追加
        topic_instructions = {
            'SELECT': """
【重要な制約】
- WHERE句、ORDER BY句、GROUP BY句、JOIN句を使わないこと
- SELECT文とFROM句のみを使用すること
- 列の選択に関する問題のみを作成すること
- 例: SELECT id, name FROM employees;
- 例: SELECT * FROM employees;
""",
    'WHERE': """
【重要な制約】
- WHERE句を必ず使用すること
- SELECT文、FROM句、WHERE句のみを使用すること
- ORDER BY句、GROUP BY句、JOIN句を使わないこと
- 条件（WHERE）に関する問題を作成すること
- 例: SELECT name FROM employees WHERE salary > 50000;
""",
    'ORDERBY': """
【重要な制約】
- ORDER BY句を必ず使用すること
- SELECT文、FROM句、ORDER BY句を使用すること
- WHERE句は任意、GROUP BY句、JOIN句を使わないこと
- 並び替え（ORDER BY）に関する問題を作成すること
- 例: SELECT name, salary FROM employees ORDER BY salary DESC;
""",
    '集約関数': """
【重要な制約】
- 集約関数（COUNT, SUM, AVG, MAX, MIN）を必ず使用すること
- GROUP BY句を使わないこと（単純集約のみ）
- SELECT文、FROM句、集約関数を使用すること
- WHERE句は任意
- 集計に関する問題を作成すること
- 例: SELECT COUNT(*) FROM employees;
- 例: SELECT AVG(salary) FROM employees;
- 例: SELECT MAX(age) FROM employees WHERE department_id = 5;
""",
    'GROUPBY': """
【重要な制約】
- GROUP BY句を必ず使用すること
- 集約関数（COUNT, SUM, AVG等）と組み合わせること
- SELECT文、FROM句、GROUP BY句を使用すること
- グループ化に関する問題を作成すること
- 例: SELECT department_id, COUNT(*) FROM employees GROUP BY department_id;
- 例: SELECT department_id, AVG(salary) FROM employees GROUP BY department_id;
""",
    'HAVING': """
【重要な制約】
- HAVING句を必ず使用すること
- GROUP BY句と集約関数を組み合わせること
- SELECT文、FROM句、GROUP BY句、HAVING句を使用すること
- グループ化後の条件指定に関する問題を作成すること
- 例: SELECT department_id, COUNT(*) FROM employees GROUP BY department_id HAVING COUNT(*) > 5;
- 例: SELECT department_id, AVG(salary) FROM employees GROUP BY department_id HAVING AVG(salary) > 50000;
""",
    'JOIN': """
【重要な制約】
- JOIN句を必ず使用すること
- 複数のテーブルを結合する問題を作成すること
- SELECT文、FROM句、JOIN句を使用すること
- テーブル結合に関する問題を作成すること
- 例: SELECT e.name, d.department_name FROM employees e JOIN departments d ON e.department_id = d.id;
"""
}
        
        topic_instruction = topic_instructions.get(topic, "")
        
        if reference_problem:
            prompt = f"""あなたはSQL学習問題の作成者です。以下の参考問題に類似した{difficulty_desc[difficulty]}SQL問題を1つ生成してください。

【テーブル定義】
{table_def}

【学習する構文】
{topic}構文

{topic_instruction}

【参考問題】
ユーザーが間違えた問題のSQL: {reference_problem}

【生成する問題の要件】
1. {topic}構文を使う問題
2. 参考問題と似た構造だが、異なる条件や列を使用
3. 難易度: {difficulty_desc[difficulty]}
4. 実際に実行可能なSQL文
5. 上記の「重要な制約」を必ず守ること

【出力形式】（必ずこの形式で出力してください）
問題文: （日本語で問題文。列名とテーブル名は括弧で英語名を併記すること）
  例: 従業員テーブル(employees)から名前(name)と給与(salary)を表示するSQL文を書いてください。
  例: 部署ID(department_id)が2である従業員(employees)の名前(name)を表示するSQL文を書いてください。
正解SQL: （正しいSQL文）
説明: （SQL文の意味を日本語で説明）
選択肢1: （正解のSQL文）
選択肢2: （誤ったSQL文）
選択肢3: （誤ったSQL文）
穴埋め問題: （穴埋め形式の問題文、{{___}}を使用）
穴埋め正解: （穴埋め部分の正解）
"""
        else:
            prompt = f"""あなたはSQL学習問題の作成者です。{topic}構文を使った{difficulty_desc[difficulty]}SQL問題を1つ生成してください。

【テーブル定義】
{table_def}

【学習する構文】
{topic}構文

{topic_instruction}

【生成する問題の要件】
1. {topic}構文を使う問題
2. 難易度: {difficulty_desc[difficulty]}
3. 実際に実行可能なSQL文
4. 初学者が理解しやすい内容
5. 上記の「重要な制約」を必ず守ること

【出力形式】（必ずこの形式で出力してください）
問題文: （日本語で問題文。列名とテーブル名は括弧で英語名を併記すること）
  例: 従業員テーブル(employees)から名前(name)と給与(salary)を表示するSQL文を書いてください。
  例: 部署ID(department_id)が2である従業員(employees)の名前(name)を表示するSQL文を書いてください。
正解SQL: （正しいSQL文）
説明: （SQL文の意味を日本語で説明）
選択肢1: （正解のSQL文）
選択肢2: （誤ったSQL文）
選択肢3: （誤ったSQL文）
穴埋め問題: （穴埋め形式の問題文、{{___}}を使用）
穴埋め正解: （穴埋め部分の正解）
"""
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            temperature=0.7,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500
        )
        
        text = response.choices[0].message.content.strip()
        
        # レスポンスをパース
        problem = {
            'id': f"{topic}_generated_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            'title': '',
            'answer_sql': '',
            'explanation': '',
            'choices': ['', '', ''],
            'blank_template': None,
            'blank_answer': None
        }
        
        lines = text.split('\n')
        for line in lines:
            if line.startswith('問題文:'):
                problem['title'] = line.replace('問題文:', '').strip()
            elif line.startswith('正解SQL:'):
                problem['answer_sql'] = line.replace('正解SQL:', '').strip()
            elif line.startswith('説明:'):
                problem['explanation'] = line.replace('説明:', '').strip()
            elif line.startswith('選択肢1:'):
                problem['choices'][0] = line.replace('選択肢1:', '').strip()
            elif line.startswith('選択肢2:'):
                problem['choices'][1] = line.replace('選択肢2:', '').strip()
            elif line.startswith('選択肢3:'):
                problem['choices'][2] = line.replace('選択肢3:', '').strip()
            elif line.startswith('穴埋め問題:'):
                problem['blank_template'] = line.replace('穴埋め問題:', '').strip()
            elif line.startswith('穴埋め正解:'):
                problem['blank_answer'] = line.replace('穴埋め正解:', '').strip()
        
        # 必須項目のチェック
        if problem['title'] and problem['answer_sql']:
            print(f"✅ 問題生成成功: {problem['id']}")
            print(f"   生成されたSQL: {problem['answer_sql']}")
            return problem
        else:
            print("❌ 問題生成失敗: 必須項目が不足")
            return None
            
    except Exception as e:
        print(f"問題生成エラー: {e}")
        return None

def validate_generated_problem(problem):
    """生成された問題の妥当性をチェック"""
    
    # 必須項目のチェック
    if not problem or not problem.get('title') or not problem.get('answer_sql'):
        print("   ❌ 検証失敗: 必須項目が不足")
        return False
    
    # 選択肢に正解が含まれているかチェック
    answer_sql = problem['answer_sql'].lower().strip().rstrip(';')
    choices = [c.lower().strip().rstrip(';') for c in problem['choices'] if c]
    
    if answer_sql not in choices:
        print(f"   ❌ 検証失敗: 選択肢に正解が含まれていません")
        print(f"      正解SQL: {answer_sql}")
        print(f"      選択肢: {choices}")
        return False
    
    # 選択肢が3つあるかチェック
    if len([c for c in problem['choices'] if c]) < 3:
        print("   ❌ 検証失敗: 選択肢が3つ未満")
        return False
    
    print("   ✅ 検証成功: 問題は適切です")
    return True

def generate_similar_problem_with_retry(topic, reference_problem=None, difficulty='medium', max_retries=3):
    """問題生成をリトライ機能付きで実行"""
    
    for attempt in range(max_retries):
        print(f"   🔄 問題生成試行 {attempt + 1}/{max_retries}")
        
        problem = generate_similar_problem(topic, reference_problem, difficulty)
        
        if problem and validate_generated_problem(problem):
            return problem
        
        print(f"   ⚠️ 試行{attempt + 1}失敗。再試行します...")
    
    print(f"   ❌ {max_retries}回試行しましたが、適切な問題を生成できませんでした。")
    return None

def get_topic_overall_accuracy(user_id, topic, format):
    """その構文・形式での全体の正答率を計算（Excel + 生成問題）"""
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        # 【修正】limitを外して全履歴を取得
        cursor.execute('''
            SELECT sql_result, meaning_result 
            FROM logs 
            WHERE user_id = ? AND problem_id LIKE ? AND format = ?
            ORDER BY timestamp DESC
        ''', (user_id, f"{topic}%", format))
        
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
        return None

def login_page():
    return """<!doctype html><html><head><title>SQL学習支援システム - ログイン</title><meta charset="utf-8"><style>body{font-family:Arial,sans-serif;margin:0;padding:0;display:flex;justify-content:center;align-items:center;min-height:100vh;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%)}.login-container{background:white;padding:40px;border-radius:10px;box-shadow:0 10px 25px rgba(0,0,0,0.2);width:100%;max-width:400px}h1{text-align:center;color:#333;margin-bottom:30px}.form-group{margin:20px 0}label{display:block;margin-bottom:8px;color:#555;font-weight:bold}input[type="text"]{width:100%;padding:12px;font-size:16px;border:2px solid #ddd;border-radius:5px;box-sizing:border-box;transition:border-color 0.3s}input[type="text"]:focus{outline:none;border-color:#667eea}input[type="submit"]{width:100%;padding:12px;font-size:18px;background-color:#667eea;color:white;border:none;border-radius:5px;cursor:pointer;transition:background-color 0.3s}input[type="submit"]:hover{background-color:#5568d3}.info{text-align:center;color:#666;font-size:14px;margin-top:20px}</style></head><body><div class="login-container"><h1>SQL学習支援システム</h1><form action='/login' method='post'><div class="form-group"><label for="user_id">ユーザーID:</label><input type="text" id="user_id" name="user_id" required placeholder="例: student001" autofocus></div><input type="submit" value="ログイン"></form><div class="info">※ ユーザーIDを入力してログインしてください</div></div></body></html>"""

def home_page():
    user_id = session.get('user_id', 'ゲスト')
    
    # 弱点分析
    weak_points = analyze_weak_points(user_id)
    weak_point_html = ""
    
    if weak_points:
        weak_point_html = "<div style='background-color:#fff3cd;padding:15px;border-radius:5px;margin:20px 0;'>"
        weak_point_html += "<h3>⚠️ 弱点が見つかりました</h3><ul>"
        for wp in weak_points[:3]:  # 上位3つ
            weak_point_html += f"<li><strong>{wp['topic']}</strong>: 正答率 {wp['accuracy']}% ({wp['correct']}/{wp['total']}問正解)</li>"
        weak_point_html += "</ul>"
        weak_point_html += "<form action='/practice' method='get'>"
        weak_point_html += "<input type='hidden' name='mode' value='weakness'>"
        weak_point_html += "<input type='submit' value='弱点克服モードで学習' style='background-color:#dc3545;color:white;padding:10px 20px;border:none;border-radius:5px;cursor:pointer;'>"
        weak_point_html += "</form></div>"
    
    return f"""<!doctype html><html><head><title>SQL学習支援システム</title><meta charset="utf-8"><style>body{{font-family:Arial,sans-serif;margin:20px}}.container{{max-width:600px;margin:0 auto}}.user-info{{background-color:#f0f0f0;padding:15px;border-radius:5px;margin-bottom:20px;display:flex;justify-content:space-between;align-items:center}}.user-name{{font-weight:bold;color:#333}}.logout-button{{background-color:#dc3545;color:white;padding:8px 15px;border:none;border-radius:5px;cursor:pointer;text-decoration:none;font-size:14px}}.logout-button:hover{{background-color:#c82333}}select,input[type="submit"]{{padding:10px;margin:5px;font-size:16px}}.form-group{{margin:15px 0}}.reset-section{{margin-top:30px;padding-top:20px;border-top:1px solid #ccc}}.reset-button{{background-color:#dc3545;color:white}}.continue-button{{background-color:#28a745;color:white}}</style></head><body><div class="container"><div class="user-info"><span class="user-name">ログイン中: {user_id}</span><a href="/logout" class="logout-button">ログアウト</a></div><h1>SQL学習支援システム</h1>{weak_point_html}<p>学習形式と出題モードを選んで開始してください。</p><form action='/practice' method='get'><div class="form-group"><label>学習形式:</label><br><select name="format"><option value="選択式">選択式</option><option value="穴埋め式">穴埋め式</option><option value="記述式">記述式</option><option value="意味説明">意味説明</option></select></div><div class="form-group"><label>出題モード:</label><br><select name="mode"><option value="adaptive">適応的学習（推奨）</option><option value="random">ランダム出題</option><option value="sequential">順番出題</option></select></div><input type="submit" value="学習開始" class="continue-button"></form><div class="reset-section"><h3>学習リセット</h3><p>最初から学習を始めたい場合はこちら：</p><form action='/reset' method='post' style="display:inline;"><input type="submit" value="学習データをリセット" class="reset-button" onclick="return confirm('学習の進行状況がリセットされます。よろしいですか？')"></form></div><form action="/history" method="get" style="margin-top:20px;"><input type="submit" value="履歴を見る"></form><form action="/stats" method="get" style="margin-top: 10px;"><input type="submit" value="学習統計を見る" style="background-color: #667eea;"></form></div></body></html>"""

@app.route("/history")
def history():
    if 'user_id' not in session:
        return redirect('/')
    user_id = session['user_id']
    
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
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
        return f"""<h1>学習履歴</h1><p>履歴の読み込み中にエラーが発生しました: {e}</p><br><a href='/home'>ホームに戻る</a>"""

@app.route("/stats")
def stats():
    if 'user_id' not in session:
        return redirect('/')
    
    user_id = session['user_id']
    stats_data = get_user_statistics(user_id)
    detailed_stats = get_detailed_statistics(user_id)
    
    if not stats_data:
        return f"""<h1>学習統計</h1><p>ユーザー「{user_id}」の学習データがありません。</p><br><a href='/home'>ホームに戻る</a>"""
    
    # 最近の履歴をHTML化
    recent_html = ""
    for log in stats_data['recent_logs']:
        timestamp, problem_id, sql_result, meaning_result = log
        result = sql_result if sql_result else meaning_result
        recent_html += f"<tr><td>{timestamp}</td><td>{problem_id}</td><td>{result}</td></tr>"
    
    # 構文別・形式別の統計をHTML化
    detailed_html = ""
    topics = ['SELECT', 'WHERE', 'ORDERBY', '集約関数', 'GROUPBY', 'HAVING', 'JOIN']
    topic_names = {
        'SELECT': 'SELECT句',
        'WHERE': 'WHERE句',
        'ORDERBY': 'ORDER BY句',
        '集約関数': '集約関数',
        'GROUPBY': 'GROUP BY句',
        'HAVING': 'HAVING句',
        'JOIN': 'JOIN句'
    }
    
    for topic in topics:
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

@app.route("/logout")
def logout():
    user_id = session.get('user_id', 'Unknown')
    session.clear()
    print(f"✅ ログアウト: {user_id}")
    return redirect('/')

@app.route("/reset", methods=["POST"])
def reset_session():
    user_id = session.get('user_id')
    session.clear()
    if user_id:
        session['user_id'] = user_id
    print("Debug - Session cleared")
    return redirect('/home')

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

HTML_TEMPLATE = """<!doctype html><html><head><title>SQL学習支援システム</title><meta charset="utf-8"><style>body{font-family:Arial,sans-serif;margin:20px}.container{max-width:800px;margin:0 auto}.format-buttons{margin:10px 0}.format-buttons form{display:inline;margin-right:5px}.format-buttons button{padding:8px 12px}.adaptive-info{background-color:#e3f2fd;padding:10px;border-radius:5px;margin:10px 0}textarea{width:100%;padding:10px;font-size:14px}input[type="submit"],button{padding:10px 20px;font-size:16px}.result{background-color:#f9f9f9;padding:15px;border-left:4px solid #007cba;margin:15px 0}pre{background-color:#f4f4f4;padding:10px;overflow-x:auto}.problem-section{margin:20px 0}.blank-template{background-color:#f0f8ff;padding:15px;border:1px solid #ccc;margin:10px 0}</style></head><body><div class="container"><h1><a href="/home" style="text-decoration:none;color:inherit" title="トップページに戻る">SQL学習支援システム</a></h1><div class="format-buttons">{% if mode != "adaptive" %}{% for f in formats %}<form method="get" action="/practice" style="display:inline;"><input type="hidden" name="format" value="{{ f }}"><input type="hidden" name="mode" value="{{ mode }}"><button type="submit" {% if f == current_format %}style="background-color:#007cba;color:white"{% endif %}>{{ f }}</button></form>{% endfor %}{% else %}<div class="adaptive-info">📚 <strong>適応的学習モード</strong> | 現在の形式: <strong>{{ current_format }}</strong> | 正答率に応じて自動的に形式が変わります</div>{% endif %}</div><form method="post"><input type="hidden" name="format" value="{{ current_format }}"><input type="hidden" name="mode" value="{{ mode }}"><div class="problem-section"><h3>問題 {{ problem.id }}: {{ current_format }}</h3>{% if current_format != "意味説明" %}<p><strong>問題:</strong> {{ problem.title }}</p>{% endif %}{% if current_format=="選択式" %}{% for choice in problem.choices %}{% if choice %}<label><input type="radio" name="student_sql" value="{{ choice }}"> {{ choice }}</label><br>{% endif %}{% endfor %}{% elif current_format=="穴埋め式" %}{% if problem.blank_template %}<div class="blank-template"><strong>穴埋め問題:</strong><br>{{ problem.blank_template }}</div><p><strong>{___} の部分に入る内容を入力してください:</strong></p><textarea name="student_sql" rows="2" cols="60" placeholder="穴埋め部分に入る内容を入力">{{ request.form.student_sql or "" }}</textarea>{% else %}<p>穴埋め問題のテンプレートが設定されていません。</p><textarea name="student_sql" rows="5" cols="80" placeholder="SQL文を入力">{{ request.form.student_sql or "" }}</textarea>{% endif %}{% elif current_format=="記述式" %}<textarea name="student_sql" rows="8" cols="80" placeholder="SQL文を入力してください">{{ request.form.student_sql or "" }}</textarea>{% elif current_format=="意味説明" %}<p><strong>以下のSQL文の意味を日本語で説明してください:</strong></p><pre>{{ problem.answer_sql }}</pre><textarea name="student_explanation" rows="6" cols="80" placeholder="SQL文の意味を日本語で詳しく説明してください">{{ request.form.student_explanation or "" }}</textarea>{% endif %}<br><br><input type="submit" value="評価する"></div></form>{% if result %}<div class="result"><h2>評価結果</h2>{% if current_format=="意味説明" %}<p><strong>結果:</strong> {{ exp_result }}</p><p><strong>フィードバック:</strong></p><pre>{{ exp_feedback }}</pre>{% if problem.explanation %}<p><strong>正解の説明:</strong></p><pre>{{ problem.explanation }}</pre>{% endif %}{% else %}<p><strong>SQL評価:</strong> {{ sql_result }}</p><p><strong>フィードバック:</strong></p><pre>{{ sql_feedback }}</pre>{% if problem.answer_sql %}<p><strong>正解のSQL:</strong></p><pre>{{ problem.answer_sql }}</pre>{% endif %}{% endif %}<form method="get" action="/practice"><input type="hidden" name="format" value="{{ current_format }}"><input type="hidden" name="mode" value="{{ mode }}"><input type="hidden" name="next" value="1"><button type="submit">次の問題に進む</button></form></div>{% endif %}</div></body></html>"""

@app.route("/practice", methods=["GET", "POST"])
def practice():
    if 'user_id' not in session:
        return redirect('/')
    
    all_problems = []
    for sheet in ["Sheet1", "Sheet2", "Sheet3", "Sheet4", "Sheet5", "Sheet6", "Sheet7"]:
        try:
            problems = load_problems(sheet)
            all_problems.extend(problems)
        except Exception as e:
            print(f"シート {sheet} の読み込みエラー: {e}")
    
    if not all_problems:
        return """<h1>エラー</h1><p>問題ファイル (problems.xlsx) が見つからないか、問題が読み込めません。</p><a href='/home'>ホームに戻る</a>"""

    mode = request.args.get("mode", session.get("mode", "random"))
    session["mode"] = mode
    
    if mode == "adaptive":
        progress = session.get('learning_progress', {
            'current_topic': 'SELECT',
            'current_format': '選択式'
        })
        current_topic = progress['current_topic']
        current_format = progress['current_format']
        
        print(f"Debug - 適応的出題: Topic={current_topic}, Format={current_format}")
    else:
        current_format = request.args.get("format", FORMATS[0])
    
    result = False
    sql_result = sql_feedback = exp_result = exp_feedback = ""

    # POST処理（評価のみ）
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

        if eval_format == "意味説明":
            if not user_exp:
                exp_result, exp_feedback = "不正解 ❌", "説明が入力されていません。"
            else:
                exp_result, exp_feedback = evaluate_meaning(user_exp, problem["explanation"])
        else:
            if not user_sql:
                sql_result, sql_feedback = "不正解 ❌", "SQL文が入力されていません。"
            else:
                sql_result, sql_feedback = evaluate_sql(user_sql, problem["answer_sql"], eval_format, problem)

        user_id = session.get('user_id', 'unknown')
        save_log(user_id, problem["id"], eval_format, user_sql, user_exp, sql_result, sql_feedback, exp_result, exp_feedback)
        
        result = True
    
    # GET処理: 次の問題に進む場合や初期表示
    else:
        if request.args.get("next") == "1":
            # 形式変更の判定
            if mode == "adaptive" and "current_problem" in session:
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
                
                accuracy_data = get_recent_accuracy(user_id, topic, current_format_for_check, limit=5, start_time=start_time)
                
                print(f"🔍 形式変更の判定: Topic={topic}, Format={current_format_for_check}")
                print(f"   この形式での回答数: {format_question_count}回")
                if start_time:
                    print(f"   形式開始時刻: {start_time}")
                if accuracy_data:
                    print(f"   直近の成績: {accuracy_data['correct']}/{accuracy_data['total']}問正解 (正答率: {accuracy_data['accuracy']}%)")
                else:
                    print(f"   まだデータなし")
                
                # テスト用: 100問に変更（通常は3）
                if format_question_count >= 5 and accuracy_data and accuracy_data['total'] >= 5:
                    
                    print(f"   → 判定開始")
                    
                    if current_format_for_check == '意味説明':
                        if accuracy_data['accuracy'] >= 70:
                            topics = ['SELECT', 'WHERE', 'ORDERBY', '集約関数', 'GROUPBY', 'HAVING', 'JOIN']
                            current_index = topics.index(topic) if topic in topics else 0
                            if current_index < len(topics) - 1:
                                next_topic = topics[current_index + 1]
                                next_format = '選択式'
                                update_learning_progress(user_id, next_topic, next_format)
                                current_format = next_format
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
                            update_learning_progress(user_id, topic, next_format)
                            current_format = next_format
                            print(f"✅ 形式変更: {current_format_for_check} → {next_format} (正答率: {accuracy_data['accuracy']}%)")
            
            if mode == "adaptive":
                progress = session.get('learning_progress', {
                    'current_topic': 'SELECT',
                    'current_format': '選択式',
                    'format_question_count': 0,
                    'format_start_time': None
                })
                topic = progress['current_topic']
                current_format = progress['current_format']
                
                print(f"Debug - GET処理: Topic={topic}, Format={current_format}")
                
                # トピックマップ
                topic_prefix_map = {
                    'SELECT': 'SELECT_',
                    'WHERE': 'WHERE_',
                    'ORDER BY': 'ORDERBY_',
                    'ORDERBY': 'ORDERBY_',
                    '集約関数': 'AGG_',  # 【追加】
                    'GROUP BY': 'GROUPBY_',
                    'GROUPBY': 'GROUPBY_',
                    'HAVING': 'HAVING_',  # 【追加】
                    'JOIN': 'JOIN_'
                }
                
                prefix = topic_prefix_map.get(topic, 'SELECT_')
                topic_problems = [p for p in all_problems if p['id'].startswith(prefix)]
                
                if topic_problems:
                    # 重複出題防止機能
                    recent_problem_ids = session.get('recent_problem_ids', {})
                    recent_ids_for_topic = recent_problem_ids.get(topic, [])
                    
                    # Excel問題から未出題の問題を取得
                    available_problems = [p for p in topic_problems if p['id'] not in recent_ids_for_topic]
                    
                    # 【修正】Excel問題が全て出題済みの場合
                    if not available_problems:
                        print(f"   📚 Excel問題は全て出題済み。")
                        
                        # 【修正】その構文の全体の正答率で判定
                        accuracy_data = get_topic_overall_accuracy(user_id, topic, current_format)
                        
                        if accuracy_data:
                            print(f"   🔍 正答率判定（全体）: {accuracy_data['correct']}/{accuracy_data['total']}問正解 = {accuracy_data['accuracy']}%")
                            
                            if accuracy_data['accuracy'] < 60:
                                # 正答率60%未満 → 生成継続
                                print(f"   🤖 正答率60%未満。GPTで問題を生成します。")
                                
                                incorrect = get_incorrect_problems(user_id, topic, limit=1)
                                reference = incorrect[0][1] if incorrect else None
                                
                                # リトライ機能を使用
                                generated_problem = generate_similar_problem_with_retry(topic, reference, 'medium', max_retries=3)
                                
                                if generated_problem:
                                    session["current_problem"] = generated_problem
                                    
                                    recent_ids_for_topic.append(generated_problem['id'])
                                    if len(recent_ids_for_topic) > 30:
                                        recent_ids_for_topic.pop(0)
                                    
                                    recent_problem_ids[topic] = recent_ids_for_topic
                                    session['recent_problem_ids'] = recent_problem_ids
                                    
                                    print(f"Debug - 生成した問題: {generated_problem['id']}")
                                else:
                                    # 生成失敗 → Excel問題再出題
                                    print(f"   ⚠️ 問題生成に3回失敗。既存問題を再出題します。")
                                    # （フォールバック処理）
                            else:
                                # 正答率60%以上 → Excel問題再出題
                                print(f"   ✅ 正答率60%以上達成。既存問題を再出題します。")
                                
                                if len(recent_ids_for_topic) > 0:
                                    recent_ids_for_topic.pop(0)
                                
                                available_problems = [p for p in topic_problems if p['id'] not in recent_ids_for_topic]
                                
                                if available_problems:
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
                                    session["current_problem"] = topic_problems[0]
                                    print(f"Debug - フォールバック: {topic_problems[0]['id']}")
                        else:
                            # データがない場合（最初の問題）
                            print(f"   ℹ️ まだデータがありません。既存問題を出題します。")
                            if len(recent_ids_for_topic) > 0:
                                recent_ids_for_topic.pop(0)
                            
                            available_problems = [p for p in topic_problems if p['id'] not in recent_ids_for_topic]
                            
                            if available_problems:
                                selected_problem = random.choice(available_problems)
                                session["current_problem"] = selected_problem
                                
                                recent_ids_for_topic.append(selected_problem['id'])
                                if len(recent_ids_for_topic) > 15:
                                    recent_ids_for_topic.pop(0)
                                
                                recent_problem_ids[topic] = recent_ids_for_topic
                                session['recent_problem_ids'] = recent_problem_ids
                                
                                print(f"Debug - 次の問題: {selected_problem['id']}")
                            else:
                                session["current_problem"] = topic_problems[0]
                    
                    else:
                        # 【通常】Excel問題がまだある場合
                        selected_problem = random.choice(available_problems)
                        session["current_problem"] = selected_problem
                        
                        # 履歴に追加
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
                
        elif "current_problem" not in session:
            session["last_format"] = current_format
            
            if mode == "adaptive":
                progress = {
                    'current_topic': 'SELECT',
                    'current_format': '選択式',
                    'format_question_count': 0,
                    'format_start_time': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                session['learning_progress'] = progress
                
                select_problems = [p for p in all_problems if p['id'].startswith('SELECT_')]
                if select_problems:
                    selected_problem = random.choice(select_problems)
                    session["current_problem"] = selected_problem
                    
                    recent_problem_ids = {'SELECT': [selected_problem['id']]}
                    session['recent_problem_ids'] = recent_problem_ids
                    
                    print(f"Debug - 初回問題: {selected_problem['id']}")
                else:
                    session["current_problem"] = all_problems[0]
            elif mode == "random":
                session["remaining_problems"] = all_problems.copy()
                random.shuffle(session["remaining_problems"])
                session["current_problem"] = session["remaining_problems"].pop()
            else:
                session["problem_index"] = 1
                session["current_problem"] = all_problems[0]
        
        elif request.args.get("format") and session.get("last_format") != current_format:
            session["last_format"] = current_format

    problem = session.get("current_problem")
    if not problem:
        problem = all_problems[0]
        session["current_problem"] = problem

    return render_template_string(HTML_TEMPLATE, problem=problem, formats=FORMATS, current_format=current_format, result=result, sql_result=sql_result, sql_feedback=sql_feedback, exp_result=exp_result, exp_feedback=exp_feedback, mode=mode, request=request)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(host='0.0.0.0', port=port, debug=False)
