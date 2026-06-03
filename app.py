import os
from datetime import timedelta
from flask import Flask, jsonify, send_from_directory, abort
from flask_login import LoginManager
from dotenv import load_dotenv
from db import init_db
from routes import main_routes, serial_bp
from auth_routes import auth_routes
from api_routes import api_routes
from user_routes import user_routes

load_dotenv()

login_manager = LoginManager()


def create_app():
    app = Flask(__name__)

    app.secret_key = os.getenv("FLASK_SECRET_KEY")
    if not app.secret_key:
        raise RuntimeError("FLASK_SECRET_KEY não definido no .env")

    is_prod = os.getenv("FLASK_ENV") == "production"
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=is_prod,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if is_prod:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    login_manager.init_app(app)
    login_manager.session_protection = "strong"

    @login_manager.user_loader
    def load_user(user_id):
        from services.user_service import get_user_by_id
        try:
            return get_user_by_id(int(user_id))
        except Exception:
            return None

    @login_manager.unauthorized_handler
    def unauthorized():
        return jsonify({"error": "Não autenticado", "authenticated": False}), 401

    with app.app_context():
        print("📦 Validando estrutura do Banco de Dados...")
        init_db()

    app.register_blueprint(main_routes)
    app.register_blueprint(serial_bp)
    app.register_blueprint(auth_routes)
    app.register_blueprint(api_routes)
    app.register_blueprint(user_routes)

    # ── Serve React frontend ──────────────────────────────────────────────────
    dist_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")

    SKIP_PREFIXES = (
        "api/", "auth/", "scanner/", "pedido", "pedidos",
        "sincronizar", "verificar_e_bipar", "serial/",
        "print/", "static/", "arquivados",
    )

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def serve_react(path):
        if any(path.startswith(s) for s in SKIP_PREFIXES):
            abort(404)
        full = os.path.join(dist_dir, path)
        if path and os.path.exists(full):
            return send_from_directory(dist_dir, path)
        return send_from_directory(dist_dir, "index.html")

    print("\n===== ROTAS REGISTRADAS =====")
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: str(r)):
        print(f"{list(rule.methods - {'HEAD','OPTIONS'})}  {rule}")
    print("=============================\n")

    return app


if __name__ == "__main__":
    if not os.path.exists("db"):
        os.makedirs("db")

    app = create_app()
    port = int(os.getenv('PORT', 8000))

    print("\n" + "=" * 40)
    print("🚀 NEXUS WMS: MEDICALD Online!")
    print(f"📍 URL: http://127.0.0.1:{port}")
    print("=" * 40 + "\n")

    app.run(debug=True, host="127.0.0.1", port=port)
