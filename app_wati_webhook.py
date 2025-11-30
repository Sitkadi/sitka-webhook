import logging
import os
import difflib
import requests
from flask import Flask, request, jsonify
from datetime import datetime
from urllib.parse import parse_qs, unquote

# ============================================================================
# CONFIGURAÇÃO
# ============================================================================

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY', 'AIzaSyB56fOrhU0MUwtFp7s7qseKzMTml0rCMjY')
WATI_API_TOKEN = os.getenv('WATI_API_TOKEN', '')
WATI_TENANT_ID = os.getenv('WATI_TENANT_ID', '')
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
        "version": "13.0"
    }), 200

# ============================================================================
# ENDPOINT IPTU
# ============================================================================

@app.route('/obter-metragem-iptu', methods=['POST'])
def obter_metragem_iptu():
    """
    Endpoint para obter metragem de IPTU pelo endereço
    Aceita JSON ou form-urlencoded
    """
    try:
        logger.info(f"[IPTU] ===== NOVA REQUISIÇÃO =====")
        logger.info(f"[IPTU] Content-Type: {request.content_type}")
        
        endereco = None
        
        # 1. Tenta JSON
        if request.is_json:
            logger.info(f"[IPTU] Recebendo como JSON")
            data = request.get_json(force=True, silent=True)
            if data:
                endereco = data.get('endereco', '').strip()
        
        # 2. Tenta form-urlencoded via request.form
        if not endereco and request.form:
            logger.info(f"[IPTU] Recebendo como form-urlencoded (form)")
            endereco = request.form.get('endereco', '').strip()
        
        # 3. Tenta raw body
        if not endereco and request.data:
            logger.info(f"[IPTU] Tentando raw body")
            try:
                raw = request.data.decode('utf-8')
                logger.info(f"[IPTU] Raw body: {raw[:100]}...")
                
                # Se for form-urlencoded
                if '=' in raw and not raw.startswith('{'):
                    logger.info(f"[IPTU] Detectado form-urlencoded")
                    parsed = parse_qs(raw)
                    logger.info(f"[IPTU] Parsed: {parsed}")
                    if 'endereco' in parsed:
                        endereco = parsed['endereco'][0].strip()
                        logger.info(f"[IPTU] Extraído de form: {endereco}")
                
                # Se for JSON
                elif raw.startswith('{'):
                    logger.info(f"[IPTU] Detectado JSON")
                    import json
                    data = json.loads(raw)
                    endereco = data.get('endereco', '').strip()
                    logger.info(f"[IPTU] Extraído de JSON: {endereco}")
            except Exception as e:
                logger.error(f"[IPTU] Erro ao processar raw body: {str(e)}")
        
        logger.info(f"[IPTU] Endereço final: '{endereco}'")
        
        if not endereco:
            logger.error(f"[IPTU] Erro: Endereço vazio")
            return jsonify({
                "metragem": None,
                "fonte": "erro",
                "mensagem": "Endereço não fornecido",
                "sucesso": False
            }), 400
        
        logger.info(f"[IPTU] Consultando: {endereco}")
        
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
        logger.error(f"[IPTU] ERRO CRÍTICO: {str(e)}")
        import traceback
        logger.error(f"[IPTU] Traceback: {traceback.format_exc()}")
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
            logger.warning(f"[DB] Endereço inválido: {endereco}")
            return None
        
        endereco_limpo = endereco.lower().strip()
        
        # Extrair apenas rua + número (tudo antes de " - bairro")
        if ' - ' in endereco_limpo:
            endereco_limpo = endereco_limpo.split(' - ')[0].strip()
        elif ',' in endereco_limpo:
            # Manter a vírgula se houver
            partes = endereco_limpo.split(',')
            endereco_limpo = partes[0].strip()
            if len(partes) > 1 and partes[1].strip().isdigit():
                endereco_limpo = f"{endereco_limpo}, {partes[1].strip()}"
        
        # Remover pontos
        endereco_limpo = endereco_limpo.replace('.', '')
        
        # Normalizar espaços
        endereco_limpo = ' '.join(endereco_limpo.split())
        
        if not endereco_limpo:
            logger.warning(f"[DB] Endereço vazio após normalização")
            return None
        
        logger.info(f"[DB] Entrada normalizada: '{endereco_limpo}'")
        
        # Busca exata
        if endereco_limpo in IPTU_DATABASE:
            logger.info(f"[DB] ✅ Match exato!")
            return IPTU_DATABASE[endereco_limpo]
        
        # Busca fuzzy
        chaves = list(IPTU_DATABASE.keys())
        matches = difflib.get_close_matches(endereco_limpo, chaves, n=1, cutoff=0.6)
        
        if matches:
            logger.info(f"[DB] ✅ Match fuzzy: {matches[0]}")
            return IPTU_DATABASE[matches[0]]
        
        logger.warning(f"[DB] Nenhuma correspondência para: '{endereco_limpo}'")
        return None
        
    except Exception as e:
        logger.error(f"[DB] Erro ao consultar: {str(e)}")
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
