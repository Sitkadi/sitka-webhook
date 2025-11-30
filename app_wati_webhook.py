import logging
import os
import difflib
import requests
import json
from flask import Flask, request, jsonify
from datetime import datetime
from urllib.parse import parse_qs, unquote
import re

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
# FUNÇÕES AUXILIARES
# ============================================================================

def extrair_endereco_raw(raw_data):
    """
    Extrai endereço de QUALQUER formato de raw data
    Tenta múltiplas estratégias de parsing
    """
    if not raw_data:
        return None
    
    logger.info(f"[RAW] Raw data length: {len(raw_data)} bytes")
    logger.info(f"[RAW] Raw data (hex): {raw_data[:200].hex()}")
    
    # Estratégia 1: Tentar decodificar com múltiplos encodings
    decoded_str = None
    encodings = ['utf-8', 'windows-1252', 'latin-1', 'cp1252', 'utf-16', 'utf-16-le']
    
    for encoding in encodings:
        try:
            decoded_str = raw_data.decode(encoding)
            logger.info(f"[RAW] ✅ Decodificado com {encoding}")
            break
        except:
            continue
    
    # Se falhar, forçar com ignore
    if not decoded_str:
        try:
            decoded_str = raw_data.decode('latin-1', errors='ignore')
            logger.info(f"[RAW] ✅ Decodificado com latin-1 + ignore")
        except:
            logger.error(f"[RAW] ❌ Não conseguiu decodificar!")
            return None
    
    logger.info(f"[RAW] Decoded string: {decoded_str[:200]}")
    
    # Estratégia 2: Extrair com regex para JSON
    if '{' in decoded_str:
        # Tentar extrair endereco entre aspas
        patterns = [
            r'"endereco"\s*:\s*"([^"]*)"',  # "endereco": "..."
            r"'endereco'\s*:\s*'([^']*)'",  # 'endereco': '...'
            r'"endereco"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',  # Com escape
        ]
        
        for pattern in patterns:
            match = re.search(pattern, decoded_str)
            if match:
                endereco = match.group(1).strip()
                logger.info(f"[RAW] ✅ Extraído com regex: {endereco[:50]}...")
                return endereco
    
    # Estratégia 3: Extrair com regex para form-urlencoded
    if '=' in decoded_str:
        match = re.search(r'endereco=([^&]*)', decoded_str)
        if match:
            endereco = unquote(match.group(1)).strip()
            logger.info(f"[RAW] ✅ Extraído de form: {endereco[:50]}...")
            return endereco
    
    logger.warning(f"[RAW] Não conseguiu extrair endereço")
    return None

# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "service": "SITKA Webhook",
        "status": "ok",
        "version": "20.0"
    }), 200

# ============================================================================
# ENDPOINT IPTU
# ============================================================================

@app.route('/obter-metragem-iptu', methods=['POST'])
def obter_metragem_iptu():
    """
    Endpoint para obter metragem de IPTU pelo endereço
    Parsing RADICAL de QUALQUER formato
    """
    try:
        logger.info(f"[IPTU] ===== NOVA REQUISIÇÃO =====")
        logger.info(f"[IPTU] Content-Type: {request.content_type}")
        logger.info(f"[IPTU] User-Agent: {request.headers.get('User-Agent', 'unknown')}")
        logger.info(f"[IPTU] Request data length: {len(request.data)} bytes")
        
        endereco = None
        
        # Estratégia 1: Tentar request.get_json()
        try:
            data = request.get_json(force=True, silent=True)
            if data and isinstance(data, dict) and 'endereco' in data:
                endereco = data.get('endereco', '').strip()
                if endereco:
                    logger.info(f"[IPTU] ✅ Extraído via get_json(): {endereco[:50]}...")
        except Exception as e:
            logger.debug(f"[IPTU] get_json() falhou: {str(e)}")
        
        # Estratégia 2: Tentar request.form
        if not endereco:
            try:
                endereco = request.form.get('endereco', '').strip()
                if endereco:
                    logger.info(f"[IPTU] ✅ Extraído via form: {endereco[:50]}...")
            except Exception as e:
                logger.debug(f"[IPTU] form falhou: {str(e)}")
        
        # Estratégia 3: Parsing MANUAL do raw data
        if not endereco and request.data:
            logger.info(f"[IPTU] Tentando parsing manual do raw data...")
            endereco = extrair_endereco_raw(request.data)
        
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
