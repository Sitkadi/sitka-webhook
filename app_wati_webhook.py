import logging
import os
import requests
import json
import io
from flask import Flask, request, jsonify

# ============================================================================
# CONFIGURAÇÃO
# ============================================================================

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY', 'AIzaSyB56fOrhU0MUwtFp7s7qseKzMTml0rCMjY')
WATI_API_TOKEN = os.getenv('WATI_API_TOKEN', '')
WATI_TENANT_ID = os.getenv('WATI_TENANT_ID', '1047617')
WATI_BASE_URL = os.getenv('WATI_BASE_URL', 'https://live-mt-server.wati.io')

# ============================================================================
# BANCO DE DADOS LOCAL DE IPTU (MOCKADO)
# ============================================================================

IPTU_DATABASE = {
    "AVENIDA PAULISTA, 1000": {"metragem": 2500},
    "AVENIDA PAULISTA, 00001000": {"metragem": 2500},
    "RUA OSCAR FREIRE, 500": {"metragem": 1800},
    "RUA OSCAR FREIRE, 00000500": {"metragem": 1800},
    "AVENIDA BRASIL, 2000": {"metragem": 3200},
    "AVENIDA BRASIL, 00002000": {"metragem": 3200},
    "RUA AUGUSTA, 800": {"metragem": 1500},
    "RUA AUGUSTA, 00000800": {"metragem": 1500},
    "AVENIDA IMIGRANTES, 3000": {"metragem": 4500},
    "AVENIDA IMIGRANTES, 00003000": {"metragem": 4500},
    "RUA 25 DE MARÇO, 1500": {"metragem": 2200},
    "RUA 25 DE MARÇO, 00001500": {"metragem": 2200},
    "R S CAETANO, 13": {"metragem": 136},
    "R S CAETANO, 00000013": {"metragem": 136},
}


def consultar_iptu(endereco):
    """Consulta IPTU no banco de dados local"""
    try:
        endereco_limpo = endereco.strip().upper()
        
        # Tentar com endereço original
        if endereco_limpo in IPTU_DATABASE:
            return IPTU_DATABASE[endereco_limpo]
        
        # Tentar com padding de zeros
        partes = endereco_limpo.split(',')
        if len(partes) >= 2:
            rua = partes[0].strip()
            numero_raw = partes[1].strip().split()[0]
            
            if numero_raw.isdigit():
                numero_padded = numero_raw.zfill(5)
                chave = f"{rua}, {numero_padded}"
                if chave in IPTU_DATABASE:
                    return IPTU_DATABASE[chave]
        
        return None
        
    except Exception as e:
        logger.error(f"[IPTU] Erro: {str(e)}")
        return None


# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "service": "SITKA Webhook",
        "status": "ok",
        "version": "37.0"
    }), 200


# ============================================================================
# ENDPOINT IPTU
# ============================================================================

@app.route('/obter-metragem-iptu', methods=['POST'])
def obter_metragem_iptu():
    """Endpoint para obter metragem de IPTU pelo endereço"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        endereco = data.get('endereco', '').strip()
        
        logger.info(f"[IPTU] Endereço: '{endereco}'")
        
        if not endereco:
            return jsonify({
                "metragem": None,
                "fonte": "erro",
                "mensagem": "Endereço não fornecido",
                "sucesso": False
            }), 400
        
        resultado = consultar_iptu(endereco)
        
        if resultado:
            logger.info(f"[IPTU] ✅ Encontrado: {resultado['metragem']} m²")
            return jsonify({
                "metragem": resultado['metragem'],
                "fonte": "iptu_local",
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


# ============================================================================
# ANÁLISE DE IMAGEM DE SATÉLITE
# ============================================================================

def enviar_imagem_wati(telefone, endereco):
    """Envia imagem de satélite para o WhatsApp via WATI API"""
    try:
        phone = telefone.replace(" ", "").replace("-", "")
        if not phone.startswith("+"):
            phone = "+" + phone
        
        logger.info(f"[SATELLITE] Obtendo imagem para {endereco}")
        
        # Forçar São Paulo, Brasil na busca com bounding box
        endereco_completo = f"{endereco}, São Paulo, Brasil"
        
        # Bounding box de São Paulo (lat/lng)
        # SW: -23.8245, -46.8134 | NE: -23.4273, -46.3569
        bounds = "23.8245,-46.8134|23.4273,-46.3569"
        
        # Baixar a imagem do Google Maps
        url = "https://maps.googleapis.com/maps/api/staticmap"
        params = {
            "center": endereco_completo,
            "bounds": bounds,
            "zoom": 18,
            "size": "600x600",
            "maptype": "satellite",
            "markers": f"color:red|{endereco}",
            "key": GOOGLE_API_KEY
        }
        
        # Primeiro tentar com bounding box
        response_img = requests.get(url, params=params, timeout=30)
        logger.info(f"[SATELLITE] Google Maps (com bounds): {response_img.status_code}")
        
        # Se falhar, tentar sem bounding box
        if response_img.status_code != 200:
            logger.warning(f"[SATELLITE] Falha com bounds, tentando sem...")
            del params['bounds']
            response_img = requests.get(url, params=params, timeout=30)
            logger.info(f"[SATELLITE] Google Maps (sem bounds): {response_img.status_code}")
        
        response_img.raise_for_status()
        
        if not response_img.headers.get('content-type', '').startswith('image'):
            logger.error("[SATELLITE] Resposta não é uma imagem")
            return False
        
        # Enviar a imagem via WATI API
        logger.info(f"[SATELLITE] Enviando para {phone}")
        
        headers = {"Authorization": f"Bearer {WATI_API_TOKEN}"}
        url_session = f"{WATI_BASE_URL}/{WATI_TENANT_ID}/api/v1/sendSessionFile/{phone}"
        
        files = {'file': ('satellite.png', io.BytesIO(response_img.content), 'image/png')}
        data = {'caption': f'Imagem de satélite: {endereco_completo}'}
        
        response_session = requests.post(url_session, headers=headers, files=files, data=data, timeout=30)
        
        logger.info(f"[SATELLITE] Resposta: {response_session.status_code}")
        
        if response_session.status_code in [200, 201]:
            logger.info(f"[SATELLITE] ✅ Imagem enviada!")
            return True
        else:
            logger.warning(f"[SATELLITE] ⚠️ Falha (status {response_session.status_code})")
            logger.warning(f"[SATELLITE] Resposta: {response_session.text}")
            return False
        
    except Exception as e:
        logger.error(f"[SATELLITE] Erro: {str(e)}")
        return False


@app.route('/analise-imagemdesatelite', methods=['POST'])
def analise_imagemdesatelite():
    """Endpoint para enviar imagem de satélite"""
    try:
        data = request.get_json(force=True, silent=True) or {}
        telefone = data.get('telefone', '').strip()
        endereco = data.get('endereco', '').strip()
        
        logger.info(f"[SATELLITE] Telefone: {telefone}, Endereço: {endereco}")
        
        if not telefone or not endereco:
            logger.error("[SATELLITE] Telefone ou endereço não fornecido")
            return jsonify({"sucesso": False, "erro": "Telefone ou endereço não fornecido"}), 400
        
        sucesso = enviar_imagem_wati(telefone, endereco)
        
        if sucesso:
            return jsonify({
                "sucesso": True,
                "mensagem": "Imagem de satélite enviada com sucesso",
                "imagemdesatelite_url": f"https://maps.googleapis.com/maps/api/staticmap?center={endereco}&zoom=18&size=600x600&maptype=satellite&key={GOOGLE_API_KEY}"
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
    logger.info("[MAIN] SITKA Webhook v37.0 iniciado")
    logger.info(f"[MAIN] {len(IPTU_DATABASE)} endereços cadastrados no banco local")
    
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
