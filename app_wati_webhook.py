"""
SITKA Webhook v10.0 - FINAL
Banco de dados local com múltiplos formatos
Aceita: Avenida, Av., Av, Rua, R., R, Rod., Rod
"""

from flask import Flask, request, jsonify
import requests
import os
from datetime import datetime
import logging
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

# Banco de dados com múltiplos formatos (Google Maps + usuário)
IPTU_DATABASE = {
    # Avenida Paulista (1000)
    "avenida paulista 1000": {"metragem": 2500, "endereco": "Avenida Paulista, 1000", "sql": "SP001"},
    "av paulista 1000": {"metragem": 2500, "endereco": "Avenida Paulista, 1000", "sql": "SP001"},
    "av. paulista 1000": {"metragem": 2500, "endereco": "Avenida Paulista, 1000", "sql": "SP001"},
    "av paulista, 1000": {"metragem": 2500, "endereco": "Avenida Paulista, 1000", "sql": "SP001"},
    "av. paulista, 1000": {"metragem": 2500, "endereco": "Avenida Paulista, 1000", "sql": "SP001"},
    "paulista 1000": {"metragem": 2500, "endereco": "Avenida Paulista, 1000", "sql": "SP001"},
    
    # Rua Oscar Freire (500)
    "rua oscar freire 500": {"metragem": 1800, "endereco": "Rua Oscar Freire, 500", "sql": "SP002"},
    "r oscar freire 500": {"metragem": 1800, "endereco": "Rua Oscar Freire, 500", "sql": "SP002"},
    "r. oscar freire 500": {"metragem": 1800, "endereco": "Rua Oscar Freire, 500", "sql": "SP002"},
    "r oscar freire, 500": {"metragem": 1800, "endereco": "Rua Oscar Freire, 500", "sql": "SP002"},
    "r. oscar freire, 500": {"metragem": 1800, "endereco": "Rua Oscar Freire, 500", "sql": "SP002"},
    "oscar freire 500": {"metragem": 1800, "endereco": "Rua Oscar Freire, 500", "sql": "SP002"},
    
    # Avenida Brasil (2000)
    "avenida brasil 2000": {"metragem": 3200, "endereco": "Avenida Brasil, 2000", "sql": "SP003"},
    "av brasil 2000": {"metragem": 3200, "endereco": "Avenida Brasil, 2000", "sql": "SP003"},
    "av. brasil 2000": {"metragem": 3200, "endereco": "Avenida Brasil, 2000", "sql": "SP003"},
    "av brasil, 2000": {"metragem": 3200, "endereco": "Avenida Brasil, 2000", "sql": "SP003"},
    "av. brasil, 2000": {"metragem": 3200, "endereco": "Avenida Brasil, 2000", "sql": "SP003"},
    "brasil 2000": {"metragem": 3200, "endereco": "Avenida Brasil, 2000", "sql": "SP003"},
    
    # Rua Augusta (800)
    "rua augusta 800": {"metragem": 1500, "endereco": "Rua Augusta, 800", "sql": "SP004"},
    "r augusta 800": {"metragem": 1500, "endereco": "Rua Augusta, 800", "sql": "SP004"},
    "r. augusta 800": {"metragem": 1500, "endereco": "Rua Augusta, 800", "sql": "SP004"},
    "r augusta, 800": {"metragem": 1500, "endereco": "Rua Augusta, 800", "sql": "SP004"},
    "r. augusta, 800": {"metragem": 1500, "endereco": "Rua Augusta, 800", "sql": "SP004"},
    "augusta 800": {"metragem": 1500, "endereco": "Rua Augusta, 800", "sql": "SP004"},
    
    # Avenida Imigrantes (3000)
    "avenida imigrantes 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    "av imigrantes 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    "av. imigrantes 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    "av imigrantes, 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    "av. imigrantes, 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    "rod imigrantes 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    "rod. imigrantes 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    "rod imigrantes, 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    "rod. imigrantes, 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    "imigrantes 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    
    # Rua 25 de Março (1500)
    "rua 25 de marco 1500": {"metragem": 2200, "endereco": "Rua 25 de Março, 1500", "sql": "SP006"},
    "rua vinte e cinco de marco 1500": {"metragem": 2200, "endereco": "Rua 25 de Março, 1500", "sql": "SP006"},
    "r 25 de marco 1500": {"metragem": 2200, "endereco": "Rua 25 de Março, 1500", "sql": "SP006"},
    "r. 25 de marco 1500": {"metragem": 2200, "endereco": "Rua 25 de Março, 1500", "sql": "SP006"},
    "r 25 de marco, 1500": {"metragem": 2200, "endereco": "Rua 25 de Março, 1500", "sql": "SP006"},
    "r. 25 de marco, 1500": {"metragem": 2200, "endereco": "Rua 25 de Março, 1500", "sql": "SP006"},
    "25 de marco 1500": {"metragem": 2200, "endereco": "Rua 25 de Março, 1500", "sql": "SP006"},
},
    "av paulista 1000": {"metragem": 2500, "endereco": "Avenida Paulista, 1000", "sql": "SP001"},
    "av. paulista 1000": {"metragem": 2500, "endereco": "Avenida Paulista, 1000", "sql": "SP001"},
    "paulista 1000": {"metragem": 2500, "endereco": "Avenida Paulista, 1000", "sql": "SP001"},
    
    # Rua Oscar Freire (500)
    "rua oscar freire 500": {"metragem": 1800, "endereco": "Rua Oscar Freire, 500", "sql": "SP002"},
    "r oscar freire 500": {"metragem": 1800, "endereco": "Rua Oscar Freire, 500", "sql": "SP002"},
    "r. oscar freire 500": {"metragem": 1800, "endereco": "Rua Oscar Freire, 500", "sql": "SP002"},
    "oscar freire 500": {"metragem": 1800, "endereco": "Rua Oscar Freire, 500", "sql": "SP002"},
    
    # Avenida Brasil (2000)
    "avenida brasil 2000": {"metragem": 3200, "endereco": "Avenida Brasil, 2000", "sql": "SP003"},
    "av brasil 2000": {"metragem": 3200, "endereco": "Avenida Brasil, 2000", "sql": "SP003"},
    "av. brasil 2000": {"metragem": 3200, "endereco": "Avenida Brasil, 2000", "sql": "SP003"},
    "brasil 2000": {"metragem": 3200, "endereco": "Avenida Brasil, 2000", "sql": "SP003"},
    
    # Rua Augusta (800)
    "rua augusta 800": {"metragem": 1500, "endereco": "Rua Augusta, 800", "sql": "SP004"},
    "r augusta 800": {"metragem": 1500, "endereco": "Rua Augusta, 800", "sql": "SP004"},
    "r. augusta 800": {"metragem": 1500, "endereco": "Rua Augusta, 800", "sql": "SP004"},
    "augusta 800": {"metragem": 1500, "endereco": "Rua Augusta, 800", "sql": "SP004"},
    
    # Avenida Imigrantes (3000)
    "avenida imigrantes 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    "av imigrantes 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    "av. imigrantes 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    "rod imigrantes 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    "rod. imigrantes 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    "imigrantes 3000": {"metragem": 4100, "endereco": "Avenida Imigrantes, 3000", "sql": "SP005"},
    
    # Rua 25 de Março (1500)
    "rua 25 de marco 1500": {"metragem": 2200, "endereco": "Rua 25 de Março, 1500", "sql": "SP006"},
    "rua vinte e cinco de marco 1500": {"metragem": 2200, "endereco": "Rua 25 de Março, 1500", "sql": "SP006"},
    "r 25 de marco 1500": {"metragem": 2200, "endereco": "Rua 25 de Março, 1500", "sql": "SP006"},
    "r. 25 de marco 1500": {"metragem": 2200, "endereco": "Rua 25 de Março, 1500", "sql": "SP006"},
    "25 de marco 1500": {"metragem": 2200, "endereco": "Rua 25 de Março, 1500", "sql": "SP006"},
}

# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "service": "SITKA Webhook",
        "status": "ok",
        "version": "10.0"
    }), 200


# ============================================================================
# OBTER METRAGEM - VERSÃO 10.0
# ============================================================================

@app.route('/obter-metragem-iptu', methods=['POST'])
def obter_metragem_iptu():
    """
    Consulta metragem do terreno via banco de dados local
    
    Body:
    {
        "endereco": "Av. Paulista, 1000 - Bela Vista, São Paulo - SP, 01310-100, Brazil"
    }
    """
    
    try:
        data = request.json or {}
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
        logger.error(f"[IPTU] Erro: {str(e)}")
        return jsonify({
            "metragem": None,
            "fonte": "erro",
            "mensagem": str(e),
            "sucesso": False
        }), 500


def consultar_banco_local(endereco):
    """
    Consulta banco de dados local com busca fuzzy
    Extrai número e nome da rua do endereço formatado do Google Maps
    """
    try:
        endereco_limpo = endereco.lower().strip()
        
        # Remover CEP e país (tudo após primeira vírgula)
        endereco_limpo = endereco_limpo.split(',')[0].strip()
        
        # Remover pontos (Av. → Av, R. → R, Rod. → Rod)
        endereco_limpo = endereco_limpo.replace('.', '')
        
        # Normalizar espaços múltiplos
        endereco_limpo = ' '.join(endereco_limpo.split())
        
        logger.info(f"[DB] Consultando: {endereco_limpo}")
        
        # Busca exata
        if endereco_limpo in IPTU_DATABASE:
            logger.info(f"[DB] ✅ Match exato!")
            return IPTU_DATABASE[endereco_limpo]
        
        # Busca fuzzy (aproximada)
        chaves = list(IPTU_DATABASE.keys())
        matches = difflib.get_close_matches(endereco_limpo, chaves, n=1, cutoff=0.6)
        
        if matches:
            logger.info(f"[DB] ✅ Match fuzzy: {matches[0]}")
            return IPTU_DATABASE[matches[0]]
        
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
        data = request.json or {}
        telefone = data.get('telefone', '').strip()
        endereco = data.get('endereco', '').strip()
        
        logger.info(f"[SATÉLITE] {endereco} → {telefone}")
        
        if not telefone or not endereco:
            return jsonify({"success": False, "error": "Dados incompletos"}), 400
        
        # Obter coordenadas
        coords = obter_coordenadas(endereco)
        if not coords:
            logger.warning(f"[MAPS] Coordenadas não encontradas para: {endereco}")
            return jsonify({"success": False, "error": "Endereço não encontrado"}), 404
        
        # Gerar URL da imagem
        url_satelite = gerar_url_satelite(coords)
        
        # Enviar via WATI
        resultado = enviar_imagem_wati(telefone, url_satelite, endereco)
        
        if resultado:
            logger.info(f"[SATÉLITE] ✅ Enviado!")
            return jsonify({"success": True, "imagemdesatelite_url": url_satelite}), 200
        else:
            logger.error(f"[SATÉLITE] Erro ao enviar")
            return jsonify({"success": False, "error": "Erro ao enviar"}), 500
        
    except Exception as e:
        logger.error(f"[SATÉLITE] Erro: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500


def obter_coordenadas(endereco):
    """Obter lat/lng via Google Maps"""
    try:
        if not GOOGLE_API_KEY:
            logger.warning("[MAPS] Google API Key não configurada")
            return None
            
        params = {"address": endereco, "key": GOOGLE_API_KEY}
        response = requests.get("https://maps.googleapis.com/maps/api/geocode/json", params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('results') and len(data['results']) > 0:
                location = data['results'][0]['geometry']['location']
                logger.info(f"[MAPS] ✅ Coordenadas: {location}")
                return {"lat": location['lat'], "lng": location['lng']}
        
        logger.warning(f"[MAPS] Sem resultados")
        return None
    except Exception as e:
        logger.error(f"[MAPS] Erro: {str(e)}")
        return None


def gerar_url_satelite(coords):
    """Gerar URL da imagem de satélite"""
    if not coords or not GOOGLE_API_KEY:
        return None
    
    params = {
        "center": f"{coords['lat']},{coords['lng']}",
        "zoom": 18,
        "size": "640x640",
        "maptype": "satellite",
        "key": GOOGLE_API_KEY
    }
    
    url = f"https://maps.googleapis.com/maps/api/staticmap?{'&'.join([f'{k}={v}' for k, v in params.items()])}"
    logger.info(f"[MAPS] URL gerada: {url[:80]}...")
    return url


def enviar_imagem_wati(telefone, url_imagem, endereco):
    """Enviar imagem via WATI"""
    try:
        if not WATI_API_TOKEN or not WATI_TENANT_ID:
            logger.warning("[WATI] Credenciais não configuradas")
            return False
            
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
    logger.info(f"🚀 SITKA Webhook v10.0 na porta {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False)
