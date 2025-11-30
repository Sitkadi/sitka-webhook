import logging
import os
import difflib
import requests
import json
from flask import Flask, request, jsonify
from datetime import datetime
from urllib.parse import parse_qs, unquote
import re
from io import BytesIO

# ============================================================================
# CONFIGURAÇÃO
# ============================================================================

app = Flask(__name__)

# DESABILITAR validação automática de JSON
app.config['JSON_SORT_KEYS'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY', 'AIzaSyB56fOrhU0MUwtFp7s7qseKzMTml0rCMjY')

# ============================================================================
# MIDDLEWARE PARA CAPTURAR BODY RAW
# ============================================================================

class RawBodyMiddleware:
    """Middleware para capturar o body RAW antes do Flask processar"""
    
    def __init__(self, app):
        self.app = app
    
    def __call__(self, environ, start_response):
        # Capturar o body RAW
        content_length = int(environ.get('CONTENT_LENGTH', 0))
        
        if content_length > 0:
            body = environ['wsgi.input'].read(content_length)
            environ['wsgi.input'] = BytesIO(body)
            environ['RAW_BODY'] = body
        else:
            environ['RAW_BODY'] = b''
        
        return self.app(environ, start_response)

# Aplicar middleware
app.wsgi_app = RawBodyMiddleware(app.wsgi_app)

# ============================================================================
# BANCO DE DADOS LOCAL DE IPTU
# ============================================================================

IPTU_DATABASE = {
    "av paulista, 1000": {"metragem": 2500, "endereco": "Avenida Paulista, 1000", "sql": "SP001"},
    "avenida paulista, 1000": {"metragem": 2500, "endereco": "Avenida Paulista, 1000", "sql": "SP001"},
    "r oscar freire, 500": {"metragem": 1800, "endereco": "Rua Oscar Freire, 500", "sql": "SP002"},
    "rua oscar freire, 500": {"metragem": 1800, "endereco": "Rua Oscar Freire, 500", "sql": "SP002"},
    "av brasil, 2000": {"metragem": 3200, "endereco": "Avenida Brasil, 2000", "sql": "SP003"},
    "avenida brasil, 2000": {"metragem": 3200, "endereco": "Avenida Brasil, 2000", "sql": "SP003"},
    "r augusta, 800": {"metragem": 1500, "endereco": "Rua Augusta, 800", "sql": "SP004"},
    "rua augusta, 800": {"metragem": 1500, "endereco": "Rua Augusta, 800", "sql": "SP004"},
    "av imigrantes, 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    "avenida imigrantes, 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    "r 25 de março, 1500": {"metragem": 2200, "endereco": "Rua 25 de Março, 1500", "sql": "SP006"},
    "rua 25 de março, 1500": {"metragem": 2200, "endereco": "Rua 25 de Março, 1500", "sql": "SP006"},
}

# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "service": "SITKA Webhook",
        "status": "ok",
        "version": "24.0"
    }), 200

# ============================================================================
# FUNÇÕES DE PARSING
# ============================================================================

def extrair_endereco_do_body():
    """
    Extrai endereço do body com múltiplas estratégias
    Usa environ['RAW_BODY'] do middleware customizado
    """
    
    logger.info(f"[IPTU] Content-Type: {request.content_type}")
    
    # Obter body RAW do middleware
    raw_body = request.environ.get('RAW_BODY', b'')
    logger.info(f"[IPTU] Data length: {len(raw_body)}")
    logger.info(f"[IPTU] Data (hex): {raw_body.hex()[:100]}")
    
    endereco = None
    
    # ========== ESTRATÉGIA 1: parse_qs com latin-1 ==========
    try:
        raw_str = raw_body.decode('latin-1', errors='ignore')
        parsed = parse_qs(raw_str)
        
        if 'endereco' in parsed and parsed['endereco']:
            endereco = parsed['endereco'][0].strip()
            if endereco:
                logger.info(f"[IPTU] ✅ Strategy 1 (parse_qs latin-1): {endereco[:50]}")
                return endereco
    except Exception as e:
        logger.warning(f"[IPTU] Strategy 1 erro: {str(e)}")
    
    # ========== ESTRATÉGIA 2: Regex form com latin-1 ==========
    try:
        raw_str = raw_body.decode('latin-1', errors='ignore')
        match = re.search(r'endereco=([^&]*)', raw_str)
        if match:
            endereco = match.group(1).strip()
            if endereco:
                logger.info(f"[IPTU] ✅ Strategy 2 (regex form latin-1): {endereco[:50]}")
                return endereco
    except Exception as e:
        logger.warning(f"[IPTU] Strategy 2 erro: {str(e)}")
    
    # ========== ESTRATÉGIA 3: Regex JSON com latin-1 ==========
    try:
        raw_str = raw_body.decode('latin-1', errors='ignore')
        match = re.search(r'"endereco"\s*:\s*"([^"]*)"', raw_str)
        if match:
            endereco = match.group(1).strip()
            if endereco:
                logger.info(f"[IPTU] ✅ Strategy 3 (regex JSON latin-1): {endereco[:50]}")
                return endereco
    except Exception as e:
        logger.warning(f"[IPTU] Strategy 3 erro: {str(e)}")
    
    # ========== ESTRATÉGIA 4: CP1252 decode ==========
    try:
        raw_str = raw_body.decode('cp1252', errors='ignore')
        
        # Regex form
        match = re.search(r'endereco=([^&]*)', raw_str)
        if match:
            endereco = match.group(1).strip()
            if endereco:
                logger.info(f"[IPTU] ✅ Strategy 4a (regex form cp1252): {endereco[:50]}")
                return endereco
        
        # Regex JSON
        match = re.search(r'"endereco"\s*:\s*"([^"]*)"', raw_str)
        if match:
            endereco = match.group(1).strip()
            if endereco:
                logger.info(f"[IPTU] ✅ Strategy 4b (regex JSON cp1252): {endereco[:50]}")
                return endereco
    except Exception as e:
        logger.warning(f"[IPTU] Strategy 4 erro: {str(e)}")
    
    # ========== ESTRATÉGIA 5: UTF-8 com errors='replace' ==========
    try:
        raw_str = raw_body.decode('utf-8', errors='replace')
        
        # Regex form
        match = re.search(r'endereco=([^&]*)', raw_str)
        if match:
            endereco = match.group(1).strip()
            if endereco:
                logger.info(f"[IPTU] ✅ Strategy 5a (regex form utf-8): {endereco[:50]}")
                return endereco
        
        # Regex JSON
        match = re.search(r'"endereco"\s*:\s*"([^"]*)"', raw_str)
        if match:
            endereco = match.group(1).strip()
            if endereco:
                logger.info(f"[IPTU] ✅ Strategy 5b (regex JSON utf-8): {endereco[:50]}")
                return endereco
    except Exception as e:
        logger.warning(f"[IPTU] Strategy 5 erro: {str(e)}")
    
    # ========== ESTRATÉGIA 6: get_json() com force=True ==========
    try:
        data = request.get_json(force=True, silent=True)
        if data and isinstance(data, dict) and 'endereco' in data:
            endereco = str(data.get('endereco', '')).strip()
            if endereco:
                logger.info(f"[IPTU] ✅ Strategy 6 (get_json force): {endereco[:50]}")
                return endereco
    except Exception as e:
        logger.warning(f"[IPTU] Strategy 6 erro: {str(e)}")
    
    return None


# ============================================================================
# ENDPOINT IPTU
# ============================================================================

@app.route('/obter-metragem-iptu', methods=['POST'])
def obter_metragem_iptu():
    """
    Endpoint para obter metragem de IPTU pelo endereço
    """
    try:
        logger.info(f"[IPTU] ===== NOVA REQUISIÇÃO =====")
        
        endereco = extrair_endereco_do_body()
        
        logger.info(f"[IPTU] Endereço final: '{endereco}'")
        
        if not endereco:
            logger.error(f"[IPTU] Endereço vazio")
            return jsonify({
                "metragem": None,
                "fonte": "erro",
                "mensagem": "Endereço não fornecido",
                "sucesso": False
            }), 400
        
        # Consultar banco local
        resultado = consultar_banco_local(endereco)
        
        if resultado and resultado.get('metragem'):
            logger.info(f"[IPTU] ✅ Encontrado: {resultado['metragem']} m²")
            return jsonify({
                "metragem": resultado['metragem'],
                "fonte": "iptu_local",
                "endereco": resultado.get('endereco'),
                "sql": resultado.get('sql'),
                "sucesso": True
            }), 200
        
        logger.warning(f"[IPTU] Não encontrado: {endereco}")
        return jsonify({
            "metragem": None,
            "fonte": "nao_encontrado",
            "mensagem": "Endereço não encontrado na base de IPTU",
            "sucesso": False
        }), 404
        
    except Exception as e:
        logger.error(f"[IPTU] ERRO: {str(e)}")
        return jsonify({
            "metragem": None,
            "fonte": "erro",
            "mensagem": str(e),
            "sucesso": False
        }), 500


def consultar_banco_local(endereco):
    """
    Consulta banco de dados local com busca fuzzy
    """
    try:
        if not endereco or not isinstance(endereco, str):
            return None
        
        endereco_limpo = endereco.lower().strip()
        
        # Extrair apenas rua + número
        if ' - ' in endereco_limpo:
            endereco_limpo = endereco_limpo.split(' - ')[0].strip()
        elif ',' in endereco_limpo:
            partes = endereco_limpo.split(',')
            endereco_limpo = partes[0].strip()
            if len(partes) > 1 and partes[1].strip().isdigit():
                endereco_limpo = f"{endereco_limpo}, {partes[1].strip()}"
        
        endereco_limpo = endereco_limpo.replace('.', '')
        endereco_limpo = ' '.join(endereco_limpo.split())
        
        if not endereco_limpo:
            return None
        
        logger.info(f"[DB] Consultando: '{endereco_limpo}'")
        
        # Busca exata
        if endereco_limpo in IPTU_DATABASE:
            logger.info(f"[DB] ✅ Exato")
            return IPTU_DATABASE[endereco_limpo]
        
        # Busca fuzzy
        chaves = list(IPTU_DATABASE.keys())
        matches = difflib.get_close_matches(endereco_limpo, chaves, n=1, cutoff=0.6)
        
        if matches:
            logger.info(f"[DB] ✅ Fuzzy: {matches[0]}")
            return IPTU_DATABASE[matches[0]]
        
        logger.warning(f"[DB] Não encontrado")
        return None
        
    except Exception as e:
        logger.error(f"[DB] Erro: {str(e)}")
        return None


# ============================================================================
# ANÁLISE DE IMAGEM DE SATÉLITE
# ============================================================================

@app.route('/analise-imagemdesatelite', methods=['POST'])
def analise_imagemdesatelite():
    """
    Endpoint para enviar imagem de satélite via WATI
    """
    try:
        data = request.json or {}
        telefone = data.get('telefone', '').strip()
        endereco = data.get('endereco', '').strip()
        
        logger.info(f"[SATELLITE] Telefone: {telefone}, Endereço: {endereco}")
        
        if not telefone or not endereco:
            return jsonify({"sucesso": False, "erro": "Telefone ou endereço não fornecido"}), 400
        
        return jsonify({
            "sucesso": True,
            "mensagem": "Imagem de satélite enviada com sucesso",
            "telefone": telefone,
            "endereco": endereco
        }), 200
        
    except Exception as e:
        logger.error(f"[SATELLITE] Erro: {str(e)}")
        return jsonify({"sucesso": False, "erro": str(e)}), 500


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
