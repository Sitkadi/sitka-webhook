import logging
import os
import difflib
import requests
import json
from flask import Flask, request, jsonify
from datetime import datetime
from urllib.parse import parse_qs, unquote
import re
import tempfile
import io

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
WATI_API_TOKEN = os.getenv('WATI_API_TOKEN', '')
WATI_TENANT_ID = os.getenv('WATI_TENANT_ID', '1047617')
WATI_BASE_URL = os.getenv('WATI_BASE_URL', 'https://live-mt-server.wati.io')

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
        "version": "27.0"
    }), 200

# ============================================================================
# FUNÇÕES DE PARSING
# ============================================================================

def extrair_endereco_do_body():
    """
    Extrai endereço do body com múltiplas estratégias
    Versão simplificada sem middleware complexo
    """
    
    logger.info(f"[IPTU] Content-Type: {request.content_type}")
    
    endereco = None
    
    # ========== ESTRATÉGIA 1: JSON direto ==========
    try:
        data = request.get_json(force=True, silent=True)
        if data and isinstance(data, dict) and 'endereco' in data:
            endereco = str(data.get('endereco', '')).strip()
            if endereco:
                logger.info(f"[IPTU] ✅ Strategy 1 (JSON): {endereco[:50]}")
                return endereco
    except Exception as e:
        logger.warning(f"[IPTU] Strategy 1 erro: {str(e)}")
    
    # ========== ESTRATÉGIA 2: Form data ==========
    try:
        if request.form:
            endereco = request.form.get('endereco', '').strip()
            if endereco:
                logger.info(f"[IPTU] ✅ Strategy 2 (Form): {endereco[:50]}")
                return endereco
    except Exception as e:
        logger.warning(f"[IPTU] Strategy 2 erro: {str(e)}")
    
    # ========== ESTRATÉGIA 3: Query params ==========
    try:
        if request.args:
            endereco = request.args.get('endereco', '').strip()
            if endereco:
                logger.info(f"[IPTU] ✅ Strategy 3 (Query): {endereco[:50]}")
                return endereco
    except Exception as e:
        logger.warning(f"[IPTU] Strategy 3 erro: {str(e)}")
    
    logger.warning(f"[IPTU] Nenhuma estratégia funcionou")
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

def enviar_imagem_wati(telefone, endereco):
    """
    Envia imagem de satélite para o WhatsApp via WATI API (sendSessionFile)
    Replica a lógica da versão offline (v42)
    """
    try:
        # Formatar telefone
        phone = telefone.replace(" ", "").replace("-", "")
        if not phone.startswith("+"):
            phone = "+" + phone
        
        logger.info(f"[SATELLITE] Passo 1: Obtendo imagem de satélite para {endereco}")
        
        # Baixar a imagem do Google Maps
        url = "https://maps.googleapis.com/maps/api/staticmap"
        params = {
            "center": endereco,
            "zoom": 18,
            "size": "600x600",
            "maptype": "satellite",
            "markers": f"color:red|{endereco}",
            "key": GOOGLE_API_KEY
        }
        
        response_img = requests.get(url, params=params, timeout=30)
        logger.info(f"[SATELLITE] Google Maps Response: {response_img.status_code}")
        response_img.raise_for_status()
        
        if not response_img.headers.get('content-type', '').startswith('image'):
            logger.error("[SATELLITE] Resposta não é uma imagem")
            return False
        
        # Enviar a imagem via WATI API (sendSessionFile)
        logger.info(f"[SATELLITE] Passo 2: Enviando imagem via WATI para {phone}")
        
        headers = {"Authorization": f"Bearer {WATI_API_TOKEN}"}
        url_session = f"{WATI_BASE_URL}/{WATI_TENANT_ID}/api/v1/sendSessionFile/{phone}"
        
        logger.info(f"[SATELLITE] URL WATI: {url_session}")
        
        files = {'file': ('satellite.png', io.BytesIO(response_img.content), 'image/png')}
        data = {'caption': f'Imagem de satélite para: {endereco}'}
        
        response_session = requests.post(url_session, headers=headers, files=files, data=data, timeout=30)
        
        logger.info(f"[SATELLITE] Resposta do envio: {response_session.status_code}")
        logger.info(f"[SATELLITE] Detalhes: {response_session.text[:200]}")
        
        if response_session.status_code in [200, 201]:
            logger.info(f"[SATELLITE] ✅ Imagem enviada com sucesso!")
            return True
        else:
            logger.warning(f"[SATELLITE] ⚠️ Falha ao enviar (status {response_session.status_code})")
            return False
        
    except Exception as e:
        logger.error(f"[SATELLITE] Erro no processo: {str(e)}")
        return False


@app.route('/analise-imagemdesatelite', methods=['POST'])
def analise_imagemdesatelite():
    """
    Endpoint para enviar imagem de satélite via WATI
    Replica a lógica da versão offline (v42)
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        telefone = data.get('telefone', '').strip()
        endereco = data.get('endereco', '').strip()
        
        logger.info(f"[SATELLITE] ===== NOVA REQUISIÇÃO =====")
        logger.info(f"[SATELLITE] Telefone: {telefone}, Endereço: {endereco}")
        
        if not telefone or not endereco:
            logger.error("[SATELLITE] Telefone ou endereço não fornecido")
            return jsonify({"sucesso": False, "erro": "Telefone ou endereço não fornecido"}), 400
        
        # Enviar a imagem
        sucesso = enviar_imagem_wati(telefone, endereco)
        
        if sucesso:
            return jsonify({
                "sucesso": True,
                "mensagem": "Imagem de satélite enviada com sucesso",
                "telefone": telefone,
                "endereco": endereco
            }), 200
        else:
            return jsonify({
                "sucesso": False,
                "erro": "Falha ao enviar imagem via WATI"
            }), 500
        
    except Exception as e:
        logger.error(f"[SATELLITE] Erro: {str(e)}")
        return jsonify({"sucesso": False, "erro": str(e)}), 500


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
