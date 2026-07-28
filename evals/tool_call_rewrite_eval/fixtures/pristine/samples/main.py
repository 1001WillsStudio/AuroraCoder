from datetime import datetime, timedelta
from typing import Optional
from fastapi.responses import HTMLResponse
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, select
from app.database import create_db_and_tables, get_session
from app.models import Tool, User
from app.auth import (
    get_password_hash, verify_password, create_access_token, 
    get_current_user, ACCESS_TOKEN_EXPIRE_MINUTES
)
from contextlib import asynccontextmanager
from pydantic import BaseModel

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield

app = FastAPI(title="ToolStore Registry", lifespan=lifespan)

# -- Pydantic Models for Auth --
class UserCreate(BaseModel):
    username: str
    email: Optional[str] = None
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

# -- Endpoints --

@app.get("/api")
def read_root():
    return {"message": "Welcome to ToolStore Registry API"}


# ── Browse HTML Template ──────────────────────────────────────────────

TOOLS_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ToolStore Registry</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root {
  --bg-primary: #0a0a0f;
  --bg-secondary: #12121a;
  --bg-tertiary: #1a1a25;
  --accent-violet: #8b5cf6;
  --accent-cyan: #06b6d4;
  --text-primary: #f4f4f5;
  --text-secondary: #a1a1aa;
  --text-tertiary: #71717a;
  --border-subtle: rgba(255,255,255,0.06);
  --border-default: rgba(255,255,255,0.1);
  --radius-md: 10px;
  --radius-lg: 16px;
  --shadow-md: 0 4px 12px rgba(0,0,0,0.4);
  --shadow-glow: 0 0 32px rgba(139,92,246,0.15);
  --font-sans: "Outfit",-apple-system,BlinkMacSystemFont,sans-serif;
  --font-mono: "JetBrains Mono","Fira Code",monospace;
}

* { margin:0; padding:0; box-sizing:border-box; }

body {
  font-family: var(--font-sans);
  background: var(--bg-primary);
  color: var(--text-primary);
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
}

/* ── Header ── */
.header {
  width: 100%%;
  text-align: center;
  padding: 64px 24px 48px;
  border-bottom: 1px solid var(--border-subtle);
}

.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-default);
  border-radius: 999px;
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 20px;
}

.badge-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%%;
  background: #22c55e;
  box-shadow: 0 0 8px rgba(34,197,94,0.5);
}

.header h1 {
  font-size: 48px;
  font-weight: 700;
  letter-spacing: -0.02em;
  background: linear-gradient(135deg, var(--accent-violet) 0%%, var(--accent-cyan) 100%%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 8px;
}

.header p {
  font-size: 16px;
  color: var(--text-secondary);
  max-width: 520px;
  margin: 0 auto;
  line-height: 1.6;
}

.count-pill {
  display: inline-block;
  margin-top: 16px;
  padding: 6px 16px;
  background: rgba(139,92,246,0.1);
  border: 1px solid rgba(139,92,246,0.25);
  border-radius: 999px;
  font-size: 14px;
  color: var(--accent-violet);
  font-weight: 500;
}

/* ── Grid ── */
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
  width: 100%%;
  max-width: 1100px;
  padding: 40px 24px 80px;
}

/* ── Tool Card ── */
.tool-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  padding: 24px;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
  position: relative;
  overflow: hidden;
}

.tool-card::before {
  content: "";
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent-violet), transparent);
  opacity: 0;
  transition: opacity 0.3s ease;
}

.tool-card:hover {
  border-color: rgba(139,92,246,0.3);
  box-shadow: var(--shadow-glow);
}

.tool-card:hover::before {
  opacity: 1;
}

.tool-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.tool-icon {
  font-size: 22px;
}

.tool-name {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  word-break: break-word;
}

.tool-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 14px;
}

.tag {
  display: inline-block;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  font-family: var(--font-mono);
}

.type-tag {
  background: rgba(6,182,212,0.12);
  color: var(--accent-cyan);
  border: 1px solid rgba(6,182,212,0.2);
}

.owner-tag {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.version-tag {
  background: rgba(139,92,246,0.1);
  color: var(--accent-violet);
  border: 1px solid rgba(139,92,246,0.2);
}

.fn-count-tag {
  background: var(--bg-tertiary);
  color: var(--text-tertiary);
}

.fn-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 12px;
}

.fn-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 400;
  font-family: var(--font-mono);
  background: rgba(139,92,246,0.08);
  color: var(--accent-violet);
  border: 1px solid rgba(139,92,246,0.15);
}

.tool-desc {
  font-size: 14px;
  line-height: 1.55;
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ── Empty State ── */
.empty-state {
  grid-column: 1 / -1;
  text-align: center;
  padding: 80px 24px;
}

.empty-icon {
  font-size: 64px;
  margin-bottom: 20px;
  opacity: 0.5;
}

.empty-state h2 {
  font-size: 22px;
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--text-primary);
}

.empty-state p {
  font-size: 14px;
  color: var(--text-secondary);
  margin-bottom: 16px;
}

.empty-state code {
  font-family: var(--font-mono);
  font-size: 13px;
  background: var(--bg-tertiary);
  padding: 8px 16px;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-default);
  color: var(--accent-cyan);
}

/* ── Footer ── */
.footer {
  width: 100%%;
  text-align: center;
  padding: 24px;
  border-top: 1px solid var(--border-subtle);
  font-size: 13px;
  color: var(--text-tertiary);
  margin-top: auto;
}

.footer a {
  color: var(--text-secondary);
  text-decoration: none;
  transition: color 0.15s;
}

.footer a:hover {
  color: var(--accent-violet);
}

/* ── Responsive ── */
@media (max-width: 640px) {
  .header h1 { font-size: 32px; }
  .grid { grid-template-columns: 1fr; padding: 24px 16px 60px; }
}
</style>
</head>
<body>

<header class="header">
  <div class="badge">
    <span class="badge-dot"></span>
    Registry Online
  </div>
  <h1>AgentToolStore</h1>
  <p>Discover toolsets built by the community.</p>
  <span class="count-pill">%d toolset(s) available</span>
</header>

<div class="grid">
  %s
</div>

<footer class="footer">
  <span>AgentToolStore Registry</span>
  <span style="margin: 0 8px">·</span>
  <span>v2.0.0</span>
</footer>

</body>
</html>
'''


@app.get("/", response_class=HTMLResponse)
def browse_tools(session: Session = Depends(get_session)):
    """
    Display-only browse page showing all published toolsets.
    """
    tools = session.exec(select(Tool)).all()

    tool_cards = ""
    for tool in tools:
        owner = tool.owner.username if tool.owner else "unknown"
        version = tool.version or "—"
        desc = (tool.description or "").replace("`", "\\`").replace("$", "\\$")[:200]
        tool_type = tool.type or "toolset"

        # Collect function names from bindings
        definition = tool.definition or {}
        bindings = definition.get("bindings", {})
        fn_names = list(bindings.keys()) if bindings else []
        fn_tags = ""
        for fn in fn_names:
            fn_tags += f'<span class="tag fn-tag">{fn}</span> '

        tool_cards += f'''
        <div class="tool-card">
            <div class="tool-header">
                <span class="tool-icon">📦</span>
                <span class="tool-name">{tool.name}</span>
            </div>
            <div class="tool-meta">
                <span class="tag type-tag">{tool_type}</span>
                <span class="tag owner-tag">@{owner}</span>
                <span class="tag version-tag">v{version}</span>
                <span class="tag fn-count-tag">{len(fn_names)} functions</span>
            </div>
            <div class="fn-list">{fn_tags}</div>
            <p class="tool-desc">{desc or "No description provided."}</p>
        </div>'''

    if not tool_cards:
        tool_cards = '''
                <div class="empty-state">
                    <div class="empty-icon">🛠️</div>
                    <h2>No toolsets published yet</h2>
                    <p>Use the CLI to publish your first toolset:</p>
                    <code>toolstore publish ./my-toolset</code>
                </div>'''

    count = len(tools)
    html = TOOLS_HTML % (count, tool_cards)
    return html

@app.post("/auth/register", response_model=Token)
def register(user: UserCreate, session: Session = Depends(get_session)):
    # Check existing
    existing = session.exec(select(User).where(User.username == user.username)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    # Create User
    hashed_pw = get_password_hash(user.password)
    db_user = User(username=user.username, email=user.email, password_hash=hashed_pw)
    session.add(db_user)
    session.commit()
    session.refresh(db_user)
    
    # Return Login Token immediately
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/auth/token", response_model=Token)
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == form_data.username)).first()
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/index.json")
def get_index(session: Session = Depends(get_session)):
    """
    Returns the full list of published toolsets in the stable dict format.

    Format: ``{"meta": {...}, "tools": {...}}`` — same shape as
    ``/online_index`` so the file on disk is always consistent.
    """
    tools = session.exec(select(Tool)).all()

    index_tools: dict[str, dict] = {}
    for tool in tools:
        tool_data = tool.definition.copy()
        tool_data["name"] = tool.name
        tool_data["description"] = tool.description
        tool_data["type"] = tool.type
        tool_data["owner"] = tool.owner.username if tool.owner else "unknown"
        # Include toolset fields for toolset-type tools
        if tool.type == "toolset":
            if tool.docker_image:
                tool_data["docker_image"] = tool.docker_image
            if tool.code:
                tool_data["code"] = tool.code
            if tool.code_base64:
                tool_data["code_base64"] = tool.code_base64
            if tool.doc:
                tool_data["doc"] = tool.doc
            if tool.env_vars:
                tool_data["env_vars"] = tool.env_vars
            if tool.requirements:
                tool_data["requirements"] = tool.requirements
        index_tools[tool.name] = tool_data

    return {
        "meta": {
            "version": "1.0",
            "count": len(tools),
            "last_updated": datetime.utcnow().isoformat(),
        },
        "tools": index_tools,
    }

@app.post("/publish")
def publish_tool(
    tool_def: dict, 
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Authenticated endpoint to publish a toolset.
    Include 'doc' (doc.md), 'code' (toolset.py),
    optional 'docker_image', 'env_vars', 'requirements'.
    """
    name = tool_def.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Tool name required")

    tool_type = "toolset"
        
    # Check if exists
    existing = session.exec(select(Tool).where(Tool.name == name)).first()
    if existing:
        if existing.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="You do not own this tool")
        existing.definition = tool_def
        existing.version = tool_def.get("version", existing.version)
        existing.description = tool_def.get("description", existing.description)
        existing.type = tool_type
        existing.updated_at = datetime.utcnow()
        existing.docker_image = tool_def.get("docker_image")
        existing.code = tool_def.get("code")
        existing.code_base64 = tool_def.get("code_base64")
        existing.doc = tool_def.get("doc")
        existing.env_vars = tool_def.get("env_vars")
        existing.requirements = tool_def.get("requirements")
        session.add(existing)
        session.commit()
        return {"success": True, "tool": name, "action": "updated"}
        
    # Create New
    tool = Tool(
        name=name,
        version=tool_def.get("version", "1.0.0"),
        type="toolset",
        description=tool_def.get("description", ""),
        definition=tool_def,
        docker_image=tool_def.get("docker_image"),
        code=tool_def.get("code"),
        code_base64=tool_def.get("code_base64"),
        doc=tool_def.get("doc"),
        env_vars=tool_def.get("env_vars"),
        requirements=tool_def.get("requirements"),
        owner_id=current_user.id
    )
    session.add(tool)
    session.commit()
    session.refresh(tool)
    return {"success": True, "tool": tool.name, "action": "created"}

@app.delete("/tools/{name}")
def delete_tool(
    name: str,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session)
):
    """
    Authenticated endpoint to delete a tool.
    """
    tool = session.exec(select(Tool).where(Tool.name == name)).first()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
        
    if tool.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="You do not own this tool")
        
    session.delete(tool)
    session.commit()
    
    return {"success": True, "tool": name, "action": "deleted"}


@app.get("/health")
def health_check(session: Session = Depends(get_session)):
    try:
        count = len(session.exec(select(Tool)).all())
        return {"status": "ok", "tools_count": count}
    except Exception as e:
        return {"status": "error", "detail": str(e)}
