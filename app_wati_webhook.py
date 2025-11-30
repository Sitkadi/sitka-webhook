"""
SITKA Webhook - Consulta IPTU via WFS Geosampa
Busca metragem pelo endereço no banco de dados público de São Paulo
"""

from flask import Flask, request, jsonify
import requests
import os
from datetime import datetime
import logging
import xml.etree.ElementTree as ET

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Variáveis
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY', '')
WATI_API_TOKEN = os.getenv('WATI_API_TOKEN', '')
WATI_TENANT_ID = os.getenv('WATI_TENANT_ID', '')
WATI_BASE_URL = os.getenv('WATI_BASE_URL', 'https://live-mt-server.wati.io')
PORT = int(os.getenv('PORT', 10000))

# WFS Geosampa
WFS_URL = "https://geosampa.prefeitura.sp.gov.br/geoserver/wfs"

# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "service": "SITKA Webhook",
        "status": "ok",
        "version": "6.0"
    }), 200


# ============================================================================
# OBTER METRAGEM VIA IPTU
# ============================================================================

@app.route('/obter-metragem-iptu', methods=['POST'])
def obter_metragem_iptu():
    """
    Consulta metragem do terreno via WFS Geosampa
    
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
        
        # Consultar WFS
        resultado = consultar_wfs_iptu(endereco)
        
        if resultado and resultado.get('metragem'):
            logger.info(f"[IPTU] ✅ Encontrado: {resultado['metragem']} m²")
            return jsonify({
                "metragem": resultado['metragem'],
                "fonte": "iptu_geosampa",
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


def consultar_wfs_iptu(endereco):
    """
    Consulta WFS Geosampa para obter metragem
    """
    try:
        # Preparar query
        endereco_limpo = endereco.split(',')[0].strip().upper()
        
        # Parâmetros WFS
        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeName": "geosampa:IPTU",
            "outputFormat": "application/json",
            "CQL_FILTER": f"UPPER(endereco) LIKE UPPER('%{endereco_limpo}%')",
            "maxfeatures": 1
        }
        
        logger.info(f"[WFS] Consultando: {endereco_limpo}")
        
        response = requests.get(WFS_URL, params=params, timeout=15)
        
        logger.info(f"[WFS] Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            if data.get('features') and len(data['features']) > 0:
                feature = data['features'][0]
                props = feature.get('properties', {})
                
                metragem = props.get('area_terreno') or props.get('AREA_TERRENO')
                
                if metragem:
                    return {
                        "metragem": float(metragem),
                        "endereco": props.get('endereco') or props.get('ENDERECO'),
                        "sql": props.get('sql') or props.get('SQL')
                    }
        
        logger.warning(f"[WFS] Nenhum resultado")
        return None
        
    except requests.exceptions.Timeout:
        logger.error("[WFS] Timeout")
        return None
    except Exception as e:
        logger.error(f"[WFS] Erro: {str(e)}")
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
    logger.info(f"🚀 SITKA Webhook v6.0 na porta {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False)
