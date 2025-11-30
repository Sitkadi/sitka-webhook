"""
SITKA Webhook - Com integração de IPTU via SDK Base dos Dados
Autor: Manus AI
Data: 30 de Novembro de 2025

Funcionalidades:
- Análise de imagem de satélite
- Consulta de zoneamento
- Obtenção automática de metragem via IPTU (SDK Python)
- Integração com WATI
"""

from flask import Flask, request, jsonify
import requests
import os
import json
from datetime import datetime
import logging
import pandas as pd

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Inicializar Flask
app = Flask(__name__)

# Variáveis de ambiente
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY', '')
WATI_API_TOKEN = os.getenv('WATI_API_TOKEN', '')
WATI_TENANT_ID = os.getenv('WATI_TENANT_ID', '')
WATI_BASE_URL = os.getenv('WATI_BASE_URL', 'https://live-mt-server.wati.io')
PORT = int(os.getenv('PORT', 10000))

# URLs das APIs
GOOGLE_MAPS_API_URL = "https://maps.googleapis.com/maps/api/geocode/json"
GOOGLE_SATELLITE_API_URL = "https://maps.googleapis.com/maps/api/staticmap"

# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.route('/health', methods=['GET'])
def health():
    """Verificar se o webhook está online"""
    return jsonify({
        "service": "SITKA Webhook",
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "version": "4.0"
    }), 200


# ============================================================================
# OBTER METRAGEM VIA IPTU (SDK)
# ============================================================================

@app.route('/obter-metragem-iptu', methods=['POST'])
def obter_metragem_iptu():
    """
    Obtém a metragem do terreno via IPTU usando SDK Base dos Dados
    
    Body esperado:
    {
        "endereco": "Avenida Paulista, 1000, São Paulo",
        "cidade": "São Paulo"
    }
    
    Retorna:
    {
        "metragem": 2500,
        "fonte": "iptu_sdk",
        "endereco": "...",
        "sql": "...",
        "sucesso": true
    }
    """
    
    try:
        data = request.json
        endereco = data.get('endereco', '').strip()
        cidade = data.get('cidade', 'São Paulo').strip()
        
        logger.info(f"[IPTU] Consultando: {endereco}, {cidade}")
        
        if not endereco:
            return jsonify({
                "metragem": None,
                "fonte": "erro",
                "mensagem": "Endereço não fornecido",
                "sucesso": False
            }), 400
        
        # Tentar consultar via SDK
        resultado = consultar_iptu_sdk(endereco, cidade)
        
        if resultado and resultado.get('metragem'):
            logger.info(f"[IPTU] ✅ Metragem encontrada: {resultado['metragem']} m²")
            return jsonify({
                "metragem": resultado['metragem'],
                "fonte": "iptu_sdk",
                "endereco": resultado.get('endereco'),
                "sql": resultado.get('sql'),
                "bairro": resultado.get('bairro'),
                "sucesso": True
            }), 200
        
        # Não encontrou
        logger.warning(f"[IPTU] ❌ Endereço não encontrado: {endereco}")
        return jsonify({
            "metragem": None,
            "fonte": "nao_encontrado",
            "mensagem": "Endereço não encontrado na base de dados de IPTU",
            "sucesso": False
        }), 404
        
    except Exception as e:
        logger.error(f"[IPTU] Erro: {str(e)}")
        return jsonify({
            "metragem": None,
            "fonte": "erro",
            "mensagem": f"Erro ao consultar IPTU: {str(e)}",
            "sucesso": False
        }), 500


def consultar_iptu_sdk(endereco, cidade):
    """
    Consulta IPTU via SQL usando Google BigQuery
    
    Usa query SQL direta no BigQuery via API
    """
    
    try:
        # Limpar endereço
        endereco_limpo = endereco.split(',')[0].strip().upper()
        
        logger.info(f"[SDK] Consultando: {endereco_limpo}")
        
        # Preparar query SQL
        query = f"""
        SELECT 
            area_terreno,
            endereco,
            sql,
            bairro,
            ano
        FROM `basedosdados.br_sp_saopaulo_geosampa_iptu.iptu`
        WHERE 
            UPPER(endereco) LIKE UPPER('%{endereco_limpo}%')
        ORDER BY ano DESC
        LIMIT 1
        """
        
        logger.info(f"[SDK] Query preparada")
        
        # Fazer requisição via BigQuery API
        url = "https://bigquery.googleapis.com/bigquery/v2/projects/basedosdados/queries"
        
        headers = {
            "Authorization": f"Bearer {GOOGLE_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "query": query,
            "useLegacySql": False,
            "maxResults": 1
        }
        
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=15
        )
        
        logger.info(f"[SDK] Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            logger.info(f"[SDK] Rows: {len(data.get('rows', []))}")
            
            # Processar resultado
            if data.get('rows') and len(data['rows']) > 0:
                row = data['rows'][0]['f']
                
                # Extrair campos
                metragem = row[0]['v'] if row[0].get('v') else None
                endereco_retorno = row[1]['v'] if row[1].get('v') else ''
                sql_imovel = row[2]['v'] if row[2].get('v') else ''
                bairro = row[3]['v'] if row[3].get('v') else ''
                
                if metragem:
                    return {
                        "metragem": float(metragem),
                        "endereco": endereco_retorno,
                        "sql": sql_imovel,
                        "bairro": bairro
                    }
        
        logger.warning(f"[SDK] Nenhuma linha encontrada ou erro {response.status_code}")
        logger.info(f"[SDK] Response: {response.text[:200]}")
        return None
        
    except requests.exceptions.Timeout:
        logger.error("[SDK] Timeout na requisição")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"[SDK] Erro na requisição: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"[SDK] Erro ao processar resposta: {str(e)}")
        return None


# ============================================================================
# ANÁLISE DE IMAGEM DE SATÉLITE
# ============================================================================

@app.route('/analise-imagemdesatelite', methods=['POST'])
def analise_imagemdesatelite():
    """
    Obtém imagem de satélite do endereço e envia via WATI
    
    Body esperado:
    {
        "telefone": "5511999999999",
        "endereco": "Avenida Paulista, 1000, São Paulo, SP"
    }
    """
    
    try:
        data = request.json
        telefone = data.get('telefone', '').strip()
        endereco = data.get('endereco', '').strip()
        
        logger.info(f"[SATÉLITE] Processando: {endereco} para {telefone}")
        
        if not telefone or not endereco:
            return jsonify({
                "success": False,
                "error": "Telefone e endereço são obrigatórios"
            }), 400
        
        # Obter coordenadas do endereço
        coords = obter_coordenadas(endereco)
        
        if not coords:
            return jsonify({
                "success": False,
                "error": "Endereço não encontrado"
            }), 404
        
        # Gerar URL da imagem de satélite
        url_satelite = gerar_url_satelite(coords)
        
        # Enviar para WATI
        resultado = enviar_imagem_wati(telefone, url_satelite, endereco)
        
        if resultado:
            return jsonify({
                "success": True,
                "imagemdesatelite_url": url_satelite,
                "mensagem_imagemdesatelite": f"Imagem de satélite de {endereco} enviada com sucesso!"
            }), 200
        else:
            return jsonify({
                "success": False,
                "error": "Erro ao enviar imagem via WATI"
            }), 500
        
    except Exception as e:
        logger.error(f"[SATÉLITE] Erro: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


def obter_coordenadas(endereco):
    """Obter latitude e longitude do endereço via Google Maps API"""
    
    try:
        params = {
            "address": endereco,
            "key": GOOGLE_API_KEY
        }
        
        response = requests.get(GOOGLE_MAPS_API_URL, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            
            if data.get('results') and len(data['results']) > 0:
                location = data['results'][0]['geometry']['location']
                return {
                    "lat": location['lat'],
                    "lng": location['lng']
                }
        
        return None
        
    except Exception as e:
        logger.error(f"[MAPS] Erro ao obter coordenadas: {str(e)}")
        return None


def gerar_url_satelite(coords):
    """Gerar URL da imagem de satélite via Google Static Maps"""
    
    if not coords:
        return None
    
    params = {
        "center": f"{coords['lat']},{coords['lng']}",
        "zoom": 18,
        "size": "640x640",
        "maptype": "satellite",
        "key": GOOGLE_API_KEY
    }
    
    return f"{GOOGLE_SATELLITE_API_URL}?{'&'.join([f'{k}={v}' for k, v in params.items()])}"


def enviar_imagem_wati(telefone, url_imagem, endereco):
    """Enviar imagem de satélite via WATI API"""
    
    try:
        # Formatar telefone
        telefone_formatado = telefone.replace('+', '').replace(' ', '')
        
        # Preparar payload
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
        
        # Headers
        headers = {
            "Authorization": f"Bearer {WATI_API_TOKEN}",
            "Content-Type": "application/json"
        }
        
        # Enviar
        response = requests.post(
            f"{WATI_BASE_URL}/api/v1/sendSessionMessage/{WATI_TENANT_ID}",
            json=payload,
            headers=headers,
            timeout=10
        )
        
        logger.info(f"[WATI] Status: {response.status_code}")
        
        return response.status_code in [200, 201]
        
    except Exception as e:
        logger.error(f"[WATI] Erro ao enviar: {str(e)}")
        return False


# ============================================================================
# ENDPOINT DE TESTE
# ============================================================================

@app.route('/analise-imagemdesatelite-test', methods=['GET'])
def test_endpoint():
    """Endpoint de teste"""
    return jsonify({
        "status": "ok",
        "mensagem": "Webhook está funcionando!",
        "endpoints": {
            "health": "/health",
            "iptu": "/obter-metragem-iptu",
            "satelite": "/analise-imagemdesatelite"
        }
    }), 200


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint não encontrado"}), 404


@app.errorhandler(500)
def server_error(error):
    return jsonify({"error": "Erro interno do servidor"}), 500


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    logger.info(f"🚀 Iniciando SITKA Webhook na porta {PORT}")
    logger.info(f"📍 Endpoints disponíveis:")
    logger.info(f"   - GET  /health")
    logger.info(f"   - POST /obter-metragem-iptu")
    logger.info(f"   - POST /analise-imagemdesatelite")
    logger.info(f"   - GET  /analise-imagemdesatelite-test")
    
    app.run(host='0.0.0.0', port=PORT, debug=False)
