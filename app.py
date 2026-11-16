from flask import Flask, jsonify, request, session, render_template, redirect
import mysql.connector
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import config
import re
import os
from datetime import date as date_type

app = Flask(__name__)
app.secret_key = config.secret_key
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
MAX_PHOTO_BYTES    = 5 * 1024 * 1024   # 5 MB

def allowed_photo(filename):
    """Return True if the file extension is in ALLOWED_EXTENSIONS."""
    return "." in filename and \
           filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def run_migrations():
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SHOW COLUMNS FROM users")
        existing = {row[0] for row in cursor.fetchall()}
        if "gender" not in existing:
            cursor.execute("ALTER TABLE users ADD COLUMN gender VARCHAR(10) NULL DEFAULT NULL")
            print("[migration] Added column: gender")
        if "date_of_birth" not in existing:
            cursor.execute("ALTER TABLE users ADD COLUMN date_of_birth DATE NULL DEFAULT NULL")
            print("[migration] Added column: date_of_birth")
        if "photo_path" not in existing:
            cursor.execute("ALTER TABLE users ADD COLUMN photo_path VARCHAR(255) NULL DEFAULT NULL")
            print("[migration] Added column: photo_path")
        if "phone" not in existing:
            cursor.execute("ALTER TABLE users ADD COLUMN phone VARCHAR(20) NULL DEFAULT NULL")
            print("[migration] Added column: phone")

        # Added: task_assignees table for multiple assignees per task
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_assignees (
                id      INT AUTO_INCREMENT PRIMARY KEY,
                task_id INT NOT NULL,
                user_id INT NOT NULL,
                UNIQUE KEY uq_task_user (task_id, user_id),
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        # Migrate any existing assigned_to values not yet in task_assignees
        cursor.execute("""
            INSERT IGNORE INTO task_assignees (task_id, user_id)
            SELECT id, assigned_to FROM tasks WHERE assigned_to IS NOT NULL
        """)
        if cursor.rowcount > 0:
            print(f"[migration] Migrated {cursor.rowcount} task assignment(s) to task_assignees")

        connection.commit()
    finally:
        cursor.close()
        connection.close()

def get_db_connection():
    return mysql.connector.connect(
        host=config.db_host,
        user=config.db_user,
        password=config.db_password,
        database=config.db_name)

def get_user():
    """Return minimal user info used by login checks and decorators."""
    if "user_id" not in session:
        return None
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    cursor.execute("SELECT id, full_name, email, role, company_name FROM users WHERE id=%s",(session["user_id"],))
    user = cursor.fetchone()
    cursor.close()
    connection.close()
    return user

def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Please login first"}), 401
        if get_user() is None:
            session.clear()
            return jsonify({"error": "Invalid session. Please log in again."}), 401
        return function(*args, **kwargs)
    return wrapper

def role_required(*allowed_roles):
    def decorator(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            user = get_user()
            if user is None:
                return jsonify({"error": "Please login first"}), 401
            if user["role"] not in allowed_roles:
                return jsonify({"error": "You do not have permission to do this"}), 403
            return function(*args, **kwargs)
        return wrapper
    return decorator

def is_project_member(cursor, project_id, user_id):
    cursor.execute(
        "SELECT id FROM project_members WHERE project_id=%s AND user_id=%s",
        (project_id, user_id)
    )
    return cursor.fetchone() is not None

def check_project_page_access(project_id):
    if "user_id" not in session:
        return redirect("/login")
    user = get_user()
    if user is None:
        session.clear()
        return redirect("/login")
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT id FROM projects WHERE id = %s", (project_id,))
        project = cursor.fetchone()
        if project is None:
            return "Project not found.", 404
        if not is_project_member(cursor, project_id, user["id"]):
            return "You do not have access to this project.", 403
    finally:
        cursor.close()
        connection.close()
    return None

@app.route("/", methods=["GET"])
def home():
    if "user_id" in session:
        # Logged-in users go straight to the authenticated dashboard
        return redirect("/dashboard")
    # Visitors see the public landing page (no login required)
    return render_template("home.html")

@app.route("/register", methods=["GET"])
def register_page():
    return render_template("register.html")

@app.route("/login", methods=["GET"])
def login_page():
    return render_template("login.html")

@app.route("/dashboard", methods=["GET"])
def dashboard_page():
    if "user_id" not in session:
        return redirect("/login")
    user = get_user()
    if user is None:
        session.clear()
        return redirect("/login")
    return render_template("dashboard.html")

@app.route("/profile-page", methods=["GET"])
def profile_html_page():
    if "user_id" not in session:
        return redirect("/login")
    user = get_user()
    if user is None:
        session.clear()
        return redirect("/login")
    return render_template("profile.html")


# Added: dedicated projects page — lists all accessible projects, project creation is here only
@app.route("/projects-page", methods=["GET"])
def projects_page():
    if "user_id" not in session:
        return redirect("/login")
    user = get_user()
    if user is None:
        session.clear()
        return redirect("/login")
    return render_template("projects.html")


# Added: About page — public, no login required, matches dashboard design
@app.route("/about", methods=["GET"])
def about_page():
    return render_template("about.html")

@app.route("/project/<int:project_id>", methods=["GET"])
def project_detail_page(project_id):
    access_error = check_project_page_access(project_id)
    if access_error is not None:
        return access_error
    return render_template("project_details.html", project_id=project_id)

@app.route("/project/<int:project_id>/members", methods=["GET"])
def project_members_page(project_id):
    access_error = check_project_page_access(project_id)
    if access_error is not None:
        return access_error
    return render_template("members.html", project_id=project_id)

@app.route("/project/<int:project_id>/tasks", methods=["GET"])
def project_tasks_page(project_id):
    access_error = check_project_page_access(project_id)
    if access_error is not None:
        return access_error
    return render_template("tasks.html", project_id=project_id)

@app.route("/register", methods=["POST"])
def register():
    data         = request.get_json(silent=True) or {}
    full_name    = str(data.get("full_name") or "").strip()
    email        = str(data.get("email") or "").strip().lower()
    password     = data.get("password") or ""
    company_name = str(data.get("company_name") or "").strip()
    branch       = str(data.get("branch") or "").strip()
    role         = str(data.get("role") or "").strip()
    gender_raw        = data.get("gender")
    date_of_birth_raw = data.get("date_of_birth")
    gender        = str(gender_raw).strip() if gender_raw else None
    date_of_birth = str(date_of_birth_raw).strip() if date_of_birth_raw else None
    allowed_roles   = ["project_manager", "team_leader", "team_member"]
    allowed_genders = ["Male", "Female", "Other"]
    if not full_name or not email or not password or not company_name or not branch or not role:
        return jsonify({"error": "All fields must be filled"}), 400
    if role not in allowed_roles:
        return jsonify({"error": "Invalid role selected"}), 400
    if gender and gender not in allowed_genders:
        return jsonify({"error": "Invalid gender selected"}), 400
    if date_of_birth:
        try:
            dob = date_type.fromisoformat(date_of_birth)
            if dob >= date_type.today():
                return jsonify({"error": "Date of birth must be in the past"}), 400
        except ValueError:
            return jsonify({"error": "Invalid date of birth format"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must contain at least 8 characters"}), 400
    password_pattern = (
        r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)"
        r"(?=.*[^A-Za-z0-9\s]).{8,}$"
    )
    if not re.match(password_pattern, password):
        return jsonify({"error": (
            "Password must contain at least 8 characters, one uppercase letter, "
            "one lowercase letter, one special character, and one number")}), 400
    hashed_password = generate_password_hash(password)
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (full_name, email, password, company_name, branch, "
            "role, gender, date_of_birth) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (full_name, email, hashed_password, company_name, branch,
             role, gender, date_of_birth)
        )
        connection.commit()
        return jsonify({"message": "User registered successfully"}), 201
    except mysql.connector.IntegrityError:
        return jsonify({"error": "This email is already registered"}), 400
    finally:
        cursor.close()
        connection.close()

@app.route("/login", methods=["POST"])
def login():
    data     = request.get_json(silent=True) or {}
    email    = data.get("email", "").strip().lower()
    password = data.get("password", "").strip()
    if not email or not password:
        return jsonify({"error": "Email and Password are required"}), 400
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE email=%s", (email,))
    user = cursor.fetchone()
    cursor.close()
    connection.close()
    if user is None or not check_password_hash(user["password"], password):
        return jsonify({"error": "Invalid email or password"}), 401
    session.clear()
    session["user_id"] = user["id"]
    return jsonify({"message": "Login successful"}), 200

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    # Redirect to public home page after logout
    return jsonify({"message": "Logout successful", "redirect": "/"}), 200

@app.route("/profile", methods=["GET"])
@login_required
def profile():
    user = get_user()
    if user is None:
        return jsonify({"error": "User not found"}), 404
    return jsonify(user)

@app.route("/user/profile", methods=["GET"])
@login_required
def get_full_profile():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id, full_name, email, role, company_name, branch, "
            "gender, date_of_birth, photo_path, phone FROM users WHERE id=%s",
            (session["user_id"],)
        )
        user = cursor.fetchone()
        if user is None:
            return jsonify({"error": "User not found"}), 404
        if user["date_of_birth"]:
            user["date_of_birth"] = user["date_of_birth"].isoformat()
        return jsonify(user)
    finally:
        cursor.close()
        connection.close()

@app.route("/user/profile", methods=["PUT"])
@login_required
def update_profile():
    data = request.get_json(silent=True) or {}
    full_name     = str(data.get("full_name") or "").strip()
    company_name  = str(data.get("company_name") or "").strip()
    branch        = str(data.get("branch") or "").strip()
    gender_raw        = data.get("gender")
    date_of_birth_raw = data.get("date_of_birth")
    gender        = str(gender_raw).strip() if gender_raw else None
    date_of_birth = str(date_of_birth_raw).strip() if date_of_birth_raw else None
    phone_raw = data.get("phone")
    phone     = str(phone_raw).strip() if phone_raw else None
    allowed_genders = ["Male", "Female", "Other"]
    if not full_name:
        return jsonify({"error": "Full name is required."}), 400
    if not company_name:
        return jsonify({"error": "Company name is required."}), 400
    if not branch:
        return jsonify({"error": "Branch is required."}), 400
    if gender and gender not in allowed_genders:
        return jsonify({"error": "Invalid gender selected."}), 400
    if date_of_birth:
        try:
            dob = date_type.fromisoformat(date_of_birth)
            if dob >= date_type.today():
                return jsonify({"error": "Date of birth must be in the past."}), 400
        except ValueError:
            return jsonify({"error": "Invalid date of birth format."}), 400
    if phone and len(phone) > 20:
        return jsonify({"error": "Phone number is too long."}), 400
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE users SET full_name=%s, company_name=%s, branch=%s, "
            "gender=%s, date_of_birth=%s, phone=%s WHERE id=%s",
            (full_name, company_name, branch, gender,
             date_of_birth, phone, session["user_id"]))
        connection.commit()
        return jsonify({"message": "Profile updated successfully."})
    finally:
        cursor.close()
        connection.close()

@app.route("/user/profile/photo", methods=["POST"])
@login_required
def upload_photo():
    if "photo" not in request.files:
        return jsonify({"error": "No file provided."}), 400
    file = request.files["photo"]
    if file.filename == "":
        return jsonify({"error": "No file selected."}), 400
    if not allowed_photo(file.filename):
        return jsonify({"error": "Only JPG, JPEG, PNG, and WEBP files are allowed."}), 400
    file_bytes = file.read()
    if len(file_bytes) > MAX_PHOTO_BYTES:
        return jsonify({"error": "File is too large. Maximum size is 5 MB."}), 400
    ext = file.filename.rsplit(".", 1)[1].lower()
    safe_filename = f"profile_{session['user_id']}.{ext}"
    upload_folder = os.path.join(app.root_path, "static", "uploads")
    os.makedirs(upload_folder, exist_ok=True)
    save_path = os.path.join(upload_folder, safe_filename)

    with open(save_path, "wb") as f:
        f.write(file_bytes)
    relative_path = f"uploads/{safe_filename}"
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("UPDATE users SET photo_path=%s WHERE id=%s",(relative_path, session["user_id"]))
        connection.commit()
        return jsonify({
            "message": "Photo uploaded successfully.",
            "photo_path": relative_path})
    finally:
        cursor.close()
        connection.close()

@app.route("/profile/search", methods=["GET"])
@login_required
def search_users():
    current_user = get_user()
    search_text  = request.args.get("search_text", "").strip()
    if not search_text:
        return jsonify([])
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id, full_name, role, company_name, branch, photo_path FROM users "
            "WHERE company_name = %s AND full_name LIKE %s AND id != %s "
            "ORDER BY full_name LIMIT 20",
            (current_user["company_name"],
             "%" + search_text + "%",
             current_user["id"])
        )
        return jsonify(cursor.fetchall())
    finally:
        cursor.close()
        connection.close()

@app.route("/projects", methods=["POST"])
@login_required
@role_required("project_manager")
def create_project():
    user = get_user()
    data         = request.get_json(silent=True) or {}
    project_name = data.get("project_name", "").strip()
    description  = data.get("description", "").strip()
    if not project_name:
        return jsonify({"error": "Project name is required"}), 400
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "INSERT INTO projects (project_name, description, created_by) "
            "VALUES (%s,%s,%s)",
            (project_name, description, user["id"])
        )
        project_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO project_members (project_id, user_id) VALUES (%s,%s)",
            (project_id, user["id"])
        )
        connection.commit()
        return jsonify({"message": "Project added successfully",
                        "project_id": project_id}), 201
    finally:
        cursor.close()
        connection.close()

@app.route("/projects", methods=["GET"])
@login_required
def get_projects():
    user = get_user()
    if user is None:
        return jsonify({"error": "user not found"}), 404
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    cursor.execute(
        "SELECT p.id, p.project_name, p.description, p.created_by, p.created_at "
        "FROM projects p JOIN project_members pm ON p.id=pm.project_id "
        "WHERE pm.user_id=%s ORDER BY p.created_at DESC",
        (user["id"],)
    )
    projects = cursor.fetchall()
    cursor.close()
    connection.close()
    return jsonify(projects)

@app.route("/projects/<int:project_id>", methods=["GET"])
@login_required
def get_project_details(project_id):
    user = get_user()
    if user is None:
        return jsonify({"error": "user not found"}), 404
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM projects WHERE id=%s", (project_id,))
        project = cursor.fetchone()
        if project is None:
            return jsonify({"error": "Project not found"}), 404
        if not is_project_member(cursor, project_id, user["id"]):
            return jsonify({"error": "You are not a member of this project"}), 403
        cursor.execute(
            "SELECT u.id, u.full_name, u.email, u.role, u.photo_path "
            "FROM users u JOIN project_members pm ON u.id=pm.user_id "
            "WHERE pm.project_id=%s",
            (project_id,)
        )
        members = cursor.fetchall()
        return jsonify({"project": project, "members": members})
    finally:
        cursor.close()
        connection.close()

@app.route("/projects/<int:project_id>/members", methods=["POST"])
@login_required
@role_required("project_manager", "team_leader")
def add_project_member(project_id):
    user     = get_user()
    data      = request.get_json(silent=True) or {}
    member_id = data.get("user_id")
    if not member_id:
        return jsonify({"error": "User ID is required."}), 400
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id, created_by FROM projects WHERE id = %s", (project_id,)
        )
        project = cursor.fetchone()
        if project is None:
            return jsonify({"error": "Project not found."}), 404
        if user["role"] == "project_manager":
            if project["created_by"] != user["id"]:
                return jsonify({"error": "You can manage only projects you created."}), 403
        if user["role"] == "team_leader":
            if not is_project_member(cursor, project_id, user["id"]):
                return jsonify({"error": "You are not a member of this project."}), 403
        cursor.execute(
            "SELECT id, company_name FROM users WHERE id = %s", (member_id,)
        )
        new_member = cursor.fetchone()
        if new_member is None:
            return jsonify({"error": "User not found."}), 404
        if new_member["company_name"] != user["company_name"]:
            return jsonify({"error": "You can add users only from your company."}), 403
        cursor.execute(
            "SELECT 1 FROM project_members WHERE project_id=%s AND user_id=%s",
            (project_id, member_id)
        )
        if cursor.fetchone():
            return jsonify({"error": "This user is already a project member."}), 409
        cursor.execute(
            "INSERT INTO project_members (project_id, user_id) VALUES (%s, %s)",
            (project_id, member_id)
        )
        connection.commit()
        return jsonify({"message": "Project member added successfully."}), 201
    finally:
        cursor.close()
        connection.close()

@app.route("/projects/<int:project_id>/tasks", methods=["POST"])
@login_required
@role_required("project_manager", "team_leader")
def create_task(project_id):
    user        = get_user()
    data        = request.get_json(silent=True) or {}
    title       = data.get("title", "").strip()
    description = data.get("description", "").strip()
    priority    = data.get("priority", "Medium")
    # Added: assignee_ids is a list of user IDs (replaces single assigned_to)
    assignee_ids = data.get("assignee_ids", [])

    if not title:
        return jsonify({"error": "Task title is required."}), 400
    allowed_priorities = ["Low", "Medium", "High", "Critical"]
    if priority not in allowed_priorities:
        return jsonify({"error": "Invalid task priority."}), 400
    # Ensure assignee_ids is a list
    if not isinstance(assignee_ids, list):
        assignee_ids = [assignee_ids] if assignee_ids else []

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, created_by FROM projects WHERE id = %s", (project_id,))
        project = cursor.fetchone()
        if project is None:
            return jsonify({"error": "Project not found."}), 404
        if user["role"] == "project_manager":
            if project["created_by"] != user["id"]:
                return jsonify({"error": "You can manage only projects you created."}), 403
        if user["role"] == "team_leader":
            if not is_project_member(cursor, project_id, user["id"]):
                return jsonify({"error": "You are not a member of this project."}), 403

        # Validate every assignee belongs to this project
        for uid in assignee_ids:
            cursor.execute(
                "SELECT 1 FROM project_members WHERE project_id=%s AND user_id=%s",
                (project_id, uid)
            )
            if cursor.fetchone() is None:
                return jsonify({"error": f"User {uid} is not a member of this project."}), 400

        # Insert the task (assigned_to kept NULL; task_assignees holds the real data)
        cursor.execute(
            "INSERT INTO tasks (project_id, title, description, priority, status, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (project_id, title, description, priority, "Pending", user["id"])
        )
        task_id = cursor.lastrowid

        # Insert all assignees into task_assignees
        for uid in assignee_ids:
            cursor.execute(
                "INSERT IGNORE INTO task_assignees (task_id, user_id) VALUES (%s,%s)",
                (task_id, uid)
            )

        connection.commit()
        return jsonify({"message": "Task created successfully.", "id": task_id}), 201
    finally:
        cursor.close()
        connection.close()

@app.route("/tasks", methods=["GET"])
@login_required
def get_tasks():
    user       = get_user()
    project_id = request.args.get("project_id", type=int)
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        if project_id is not None:
            # All project members can view all tasks — only check membership
            if not is_project_member(cursor, project_id, user["id"]):
                return jsonify({"error": "You do not have access to this project."}), 403
            cursor.execute(
                "SELECT id, project_id, title, description, priority, status "
                "FROM tasks WHERE project_id=%s ORDER BY id DESC",
                (project_id,)
            )
        else:
            if user["role"] == "team_member":
                # Team members see tasks they are assigned to across all their projects
                cursor.execute(
                    "SELECT DISTINCT t.id, t.project_id, t.title, t.description, "
                    "t.priority, t.status FROM tasks t "
                    "INNER JOIN task_assignees ta ON ta.task_id=t.id "
                    "WHERE ta.user_id=%s ORDER BY t.id DESC",
                    (user["id"],)
                )
            else:
                cursor.execute(
                    "SELECT DISTINCT t.id, t.project_id, t.title, t.description, "
                    "t.priority, t.status FROM tasks t "
                    "INNER JOIN project_members pm ON pm.project_id=t.project_id "
                    "WHERE pm.user_id=%s ORDER BY t.id DESC",
                    (user["id"],)
                )

        tasks = cursor.fetchall()

        # Attach assignee names to each task
        for task in tasks:
            cursor.execute(
                "SELECT u.id, u.full_name FROM users u "
                "INNER JOIN task_assignees ta ON ta.user_id=u.id "
                "WHERE ta.task_id=%s ORDER BY u.full_name",
                (task["id"],)
            )
            task["assignees"] = cursor.fetchall()

        return jsonify(tasks)
    finally:
        cursor.close()
        connection.close()

@app.route("/tasks/<int:task_id>", methods=["PATCH"])
@login_required
def update_task(task_id):
    user       = get_user()
    data       = request.get_json(silent=True) or {}
    new_status = data.get("status")
    allowed_statuses = ["Pending", "In Progress", "Completed", "Blocked"]
    if new_status not in allowed_statuses:
        return jsonify({"error": "Invalid task status."}), 400
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, project_id, created_by FROM tasks WHERE id=%s", (task_id,))
        task = cursor.fetchone()
        if task is None:
            return jsonify({"error": "Task not found."}), 404
        if not is_project_member(cursor, task["project_id"], user["id"]):
            return jsonify({"error": "You do not have access to this task."}), 403

        # Check if user is assigned to this task
        cursor.execute(
            "SELECT 1 FROM task_assignees WHERE task_id=%s AND user_id=%s",
            (task_id, user["id"])
        )
        is_assignee = cursor.fetchone() is not None

        # PM who created the project, TL in the project, or any assignee can update status
        cursor.execute("SELECT created_by FROM projects WHERE id=%s", (task["project_id"],))
        project = cursor.fetchone()
        is_pm_owner = (user["role"] == "project_manager" and project["created_by"] == user["id"])
        is_tl = (user["role"] == "team_leader" and is_project_member(cursor, task["project_id"], user["id"]))

        if not (is_pm_owner or is_tl or is_assignee):
            return jsonify({"error": "You are neither creator of this task nor it is assigned to you nor a team leader."}), 403

        cursor.execute("UPDATE tasks SET status=%s WHERE id=%s", (new_status, task_id))
        connection.commit()
        return jsonify({"message": "Task status updated successfully."})
    finally:
        cursor.close()
        connection.close()

@app.route("/tasks/<int:task_id>", methods=["PUT"])
@login_required
def edit_task(task_id):
    user        = get_user()
    data        = request.get_json(silent=True) or {}
    title       = data.get("title", "").strip()
    description = data.get("description", "").strip()
    priority    = data.get("priority", "").strip()
    status      = data.get("status", "").strip()
    # Added: list of assignee user IDs replaces single assigned_to
    assignee_ids = data.get("assignee_ids", [])
    if not isinstance(assignee_ids, list):
        assignee_ids = [assignee_ids] if assignee_ids else []

    allowed_priorities = ["Low", "Medium", "High", "Critical"]
    allowed_statuses   = ["Pending", "In Progress", "Completed", "Blocked"]

    if not title:
        return jsonify({"error": "Task title is required."}), 400
    if priority not in allowed_priorities:
        return jsonify({"error": "Invalid task priority."}), 400
    if status not in allowed_statuses:
        return jsonify({"error": "Invalid task status."}), 400
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id, project_id, created_by FROM tasks WHERE id=%s", (task_id,)
        )
        task = cursor.fetchone()
        if task is None:
            return jsonify({"error": "Task not found."}), 404
        if not is_project_member(cursor, task["project_id"], user["id"]):
            return jsonify({"error": "You do not have access to this task."}), 403
        cursor.execute(
            "SELECT created_by FROM projects WHERE id=%s", (task["project_id"],)
        )
        project = cursor.fetchone()
        is_pm_owner = (user["role"] == "project_manager" and project["created_by"] == user["id"])
        is_tl = (user["role"] == "team_leader" and is_project_member(cursor, task["project_id"], user["id"]))
        cursor.execute(
            "SELECT 1 FROM task_assignees WHERE task_id=%s AND user_id=%s",
            (task_id, user["id"])
        )
        is_assignee = cursor.fetchone() is not None
        if not (is_pm_owner or is_tl or is_assignee):
            return jsonify({"error": "You are neither creator of this task nor it is assigned to you nor a team leader."}), 403
        # Validate new assignees belong to the project
        for uid in assignee_ids:
            cursor.execute(
                "SELECT 1 FROM project_members WHERE project_id=%s AND user_id=%s",
                (task["project_id"], uid)
            )
            if cursor.fetchone() is None:
                return jsonify({"error": f"User {uid} is not a member of this project."}), 400
        cursor.execute(
            "UPDATE tasks SET title=%s, description=%s, priority=%s, status=%s WHERE id=%s",
            (title, description, priority, status, task_id)
        )
        # Replace assignees
        cursor.execute("DELETE FROM task_assignees WHERE task_id=%s", (task_id,))
        for uid in assignee_ids:
            cursor.execute(
                "INSERT IGNORE INTO task_assignees (task_id, user_id) VALUES (%s,%s)",
                (task_id, uid)
            )
        connection.commit()
        return jsonify({"message": "Task updated successfully."})
    finally:
        cursor.close()
        connection.close()


# Added: remove a member from a project — project_manager only.
# Only the PM who created the project may remove members.
# Does NOT delete the user account or remove them from other projects.
@app.route("/projects/<int:project_id>/members/<int:member_id>", methods=["DELETE"])
@login_required
@role_required("project_manager")
def remove_project_member(project_id, member_id):
    user = get_user()
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        # Verify the project exists and this PM created it
        cursor.execute("SELECT id, created_by FROM projects WHERE id=%s", (project_id,))
        project = cursor.fetchone()
        if project is None:
            return jsonify({"error": "Project not found."}), 404
        if project["created_by"] != user["id"]:
            return jsonify({"error": "You can only manage projects you created."}), 403

        # Prevent the PM from removing themselves — they must remain to manage the project
        if member_id == user["id"]:
            return jsonify({"error": "You cannot remove yourself from your own project."}), 400

        # Check the membership actually exists before deleting
        cursor.execute(
            "SELECT 1 FROM project_members WHERE project_id=%s AND user_id=%s",
            (project_id, member_id)
        )
        if cursor.fetchone() is None:
            return jsonify({"error": "This user is not a member of the project."}), 404

        # Safe DELETE — only removes this user from this project, nothing else
        cursor.execute(
            "DELETE FROM project_members WHERE project_id=%s AND user_id=%s",
            (project_id, member_id)
        )
        connection.commit()
        return jsonify({"message": "Member removed successfully."})
    finally:
        cursor.close()
        connection.close()


# Added: lets any authenticated user remove a project from their own account.
# This only deletes the calling user's row in project_members — the project,
# its tasks, and all other members remain completely untouched.
@app.route("/projects/<int:project_id>/leave", methods=["DELETE"])
@login_required
def leave_project(project_id):
    user = get_user()
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        # Confirm the project exists
        cursor.execute("SELECT id FROM projects WHERE id=%s", (project_id,))
        if cursor.fetchone() is None:
            return jsonify({"error": "Project not found."}), 404

        # Confirm the user is actually a member before trying to remove
        cursor.execute(
            "SELECT 1 FROM project_members WHERE project_id=%s AND user_id=%s",
            (project_id, user["id"])
        )
        if cursor.fetchone() is None:
            return jsonify({"error": "You are not a member of this project."}), 404

        # Remove only this user's membership — nothing else is touched
        cursor.execute(
            "DELETE FROM project_members WHERE project_id=%s AND user_id=%s",
            (project_id, user["id"])
        )
        connection.commit()
        return jsonify({"message": "Project removed from your account."})
    finally:
        cursor.close()
        connection.close()

if __name__ == "__main__":
    run_migrations()
    app.run(debug=True)