import app

app.app.secret_key = app.app.secret_key or "qa-only"
c = app.app.test_client()
with c.session_transaction() as session:
    session["authenticated"] = True
    session["user"] = "qa"

response = c.get("/api/lab/profile/workspace?profile=default")
assert response.status_code == 200, response.data
workspace = response.get_json()
assert workspace["ok"] and len(workspace["docs"]) == 4 and workspace["skills"]
response = c.get("/api/lab/profile/document?profile=default&kind=soul")
assert response.status_code == 200 and response.get_json()["content"]
assert c.get("/api/lab/profile/document?profile=../bad&kind=soul").status_code == 400
assert c.post("/api/lab/profile/document", json={"profile": "default", "kind": "config", "content": "- invalid"}).status_code == 400
print("profile editor API checks OK", len(workspace["skills"]), "skills")
