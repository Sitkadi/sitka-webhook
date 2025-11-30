"""
SITKA Webhook - Consulta IPTU via Banco de Dados Local
Banco de dados pré-carregado com endereços de São Paulo
"""

from flask import Flask, request, jsonify
import requests
import os
from datetime import datetime
import logging
import sqlite3
import difflib

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Variáveis
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY', '')
WATI_API_TOKEN = os.getenv('WATI_API_TOKEN', '')
WATI_TENANT_ID = os.getenv('WATI_TENANT_ID', '')
WATI_BASE_URL = os.getenv('WATI_BASE_URL', 'https://live-mt-server.wati.io')
PORT = int(os.getenv('PORT', 10000))

# Banco de dados local
DB_PATH = '/tmp/iptu_cache.db'

# Dados de exemplo (em produção, seria carregado do banco)
IPTU_DATA = {
    "avenida paulista": {"metragem": 2500, "endereco": "Avenida Paulista, 1000", "sql": "123456"},
    "rua oscar freire": {"metragem": 1800, "endereco": "Rua Oscar Freire, 500", "sql": "234567"},
    "av brasil": {"metragem": 3200, "endereco": "Avenida Brasil, 2000", "sql": "345678"},
    "rua augusta": {"metragem": 1500, "endereco": "Rua Augusta, 800", "sql": "456789"},
    "av paulista": {"metragem": 2500, "endereco": "Avenida Paulista, 1000", "sql": "123456"},
}

# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "service": "SITKA Webhook",
        "status": "ok",
        "version": "8.0"
    }), 200


# ============================================================================
# OBTER METRAGEM VIA BANCO LOCAL
# ============================================================================

@app.route('/obter-metragem-iptu', methods=['POST'])
def obter_metragem_iptu():
    """
    Consulta metragem do terreno via banco de dados local
    
    Body:
    {
        "endereco": "Avenida Paulista, 1000",
        "cidade": "São Paulo"
    }
    """
    
    try:
        data = request.json
        endereco = data.get('endereco', '').strip()
        
        logger.info(f"[IPTU] Consultando: {endereco}")
        
        if not endereco:
            return jsonify({
                "metragem": None,
                "fonte": "erro",
                "mensagem": "Endereço não fornecido",
                "sucesso": False
            }), 400
        
        # Consultar banco local
        resultado = consultar_iptu_local(endereco)
        
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
        logger.error(f"[IPTU] Erro: {str(e)}")
        return jsonify({
            "metragem": None,
            "fonte": "erro",
            "mensagem": str(e),
            "sucesso": False
        }), 500


def consultar_iptu_local(endereco):
    """
    Consulta banco de dados local com busca fuzzy
    """
    try:
        endereco_limpo = endereco.lower().strip()
        
        logger.info(f"[DB] Consultando: {endereco_limpo}")
        
        # Busca exata
        if endereco_limpo in IPTU_DATA:
            return IPTU_DATA[endereco_limpo]
        
        # Busca fuzzy (aproximada)
        chaves = list(IPTU_DATA.keys())
        matches = difflib.get_close_matches(endereco_limpo, chaves, n=1, cutoff=0.6)
        
        if matches:
            logger.info(f"[DB] Match fuzzy: {matches[0]}")
            return IPTU_DATA[matches[0]]
        
        logger.warning(f"[DB] Nenhuma correspondência")
        return None
        
    except Exception as e:
        logger.error(f"[DB] Erro: {str(e)}")
        return None


# ============================================================================
# ANÁLISE DE IMAGEM DE SATÉLITE
# ============================================================================

@app.route('/analise-imagemdesatelite', methods=['POST'])
def analise_imagemdesatelite():
    """Envia imagem de satélite via WATI"""
    
    try:
        data = request.json
        telefone = data.get('telefone', '').strip()
        endereco = data.get('endereco', '').strip()
        
        logger.info(f"[SATÉLITE] {endereco} → {telefone}")
        
        if not telefone or not endereco:
            return jsonify({"success": False, "error": "Dados incompletos"}), 400
        
        # Obter coordenadas
        coords = obter_coordenadas(endereco)
        if not coords:
            return jsonify({"success": False, "error": "Endereço não encontrado"}), 404
        
        # Gerar URL da imagem
        url_satelite = gerar_url_satelite(coords)
        
        # Enviar via WATI
        resultado = enviar_imagem_wati(telefone, url_satelite, endereco)
        
        if resultado:
            logger.info(f"[SATÉLITE] ✅ Enviado!")
            return jsonify({"success": True, "imagemdesatelite_url": url_satelite}), 200
        else:
            return jsonify({"success": False, "error": "Erro ao enviar"}), 500
        
    except Exception as e:
        logger.error(f"[SATÉLITE] Erro: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


def obter_coordenadas(endereco):
    """Obter lat/lng via Google Maps"""
    try:
        params = {"address": endereco, "key": GOOGLE_API_KEY}
        response = requests.get("https://maps.googleapis.com/maps/api/geocode/json", params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('results') and len(data['results']) > 0:
                location = data['results'][0]['geometry']['location']
                return {"lat": location['lat'], "lng": location['lng']}
        return None
    except Exception as e:
        logger.error(f"[MAPS] Erro: {str(e)}")
        return None


def gerar_url_satelite(coords):
    """Gerar URL da imagem de satélite"""
    if not coords:
        return None
    
    params = {
        "center": f"{coords['lat']},{coords['lng']}",
        "zoom": 18,
        "size": "640x640",
        "maptype": "satellite",
        "key": GOOGLE_API_KEY
    }
    
    return f"https://maps.googleapis.com/maps/api/staticmap?{'&'.join([f'{k}={v}' for k, v in params.items()])}"


def enviar_imagem_wati(telefone, url_imagem, endereco):
    """Enviar imagem via WATI"""
    try:
        telefone_formatado = telefone.replace('+', '').replace(' ', '')
        
        payload = {
            "customUserMessage": {
                "phoneNumber": telefone_formatado,
                "message": f"🛰️ Imagem de satélite de {endereco}",
                "media": {
                    "url": url_imagem,
                    "type": "image"
                }
            }
        }
        
        headers = {
            "Authorization": f"Bearer {WATI_API_TOKEN}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(
            f"{WATI_BASE_URL}/api/v1/sendSessionMessage/{WATI_TENANT_ID}",
            json=payload,
            headers=headers,
            timeout=10
        )
        
        logger.info(f"[WATI] Status: {response.status_code}")
        return response.status_code in [200, 201]
        
    except Exception as e:
        logger.error(f"[WATI] Erro: {str(e)}")
        return False


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    logger.info(f"🚀 SITKA Webhook v8.0 na porta {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False)
