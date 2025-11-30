import logging
import os
import requests
import json
import io
from flask import Flask, request, jsonify
from datetime import datetime

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
    "AVENIDA PAULISTA, 1000": {"metragem": 2500, "rua": "AVENIDA PAULISTA", "numero": "1000"},
    "AVENIDA PAULISTA, 00001000": {"metragem": 2500, "rua": "AVENIDA PAULISTA", "numero": "01000"},
    "RUA OSCAR FREIRE, 500": {"metragem": 1800, "rua": "RUA OSCAR FREIRE", "numero": "500"},
    "RUA OSCAR FREIRE, 00000500": {"metragem": 1800, "rua": "RUA OSCAR FREIRE", "numero": "00500"},
    "AVENIDA BRASIL, 2000": {"metragem": 3200, "rua": "AVENIDA BRASIL", "numero": "2000"},
    "AVENIDA BRASIL, 00002000": {"metragem": 3200, "rua": "AVENIDA BRASIL", "numero": "02000"},
    "RUA AUGUSTA, 800": {"metragem": 1500, "rua": "RUA AUGUSTA", "numero": "800"},
    "RUA AUGUSTA, 00000800": {"metragem": 1500, "rua": "RUA AUGUSTA", "numero": "00800"},
    "AVENIDA IMIGRANTES, 3000": {"metragem": 4500, "rua": "AVENIDA IMIGRANTES", "numero": "3000"},
    "AVENIDA IMIGRANTES, 00003000": {"metragem": 4500, "rua": "AVENIDA IMIGRANTES", "numero": "03000"},
    "RUA 25 DE MARÇO, 1500": {"metragem": 2200, "rua": "RUA 25 DE MARÇO", "numero": "1500"},
    "RUA 25 DE MARÇO, 00001500": {"metragem": 2200, "rua": "RUA 25 DE MARÇO", "numero": "01500"},
    "R S CAETANO, 13": {"metragem": 136, "rua": "R S CAETANO", "numero": "13"},
    "R S CAETANO, 00000013": {"metragem": 136, "rua": "R S CAETANO", "numero": "00013"},
}


def consultar_iptu(endereco):
    """
    Consulta IPTU no banco de dados local
    """
    try:
        # Limpar endereço
        endereco_limpo = endereco.strip().upper()
        
        # Extrair rua e número
        partes = endereco_limpo.split(',')
        
        if len(partes) < 2:
            logger.warning(f"[IPTU] Formato inválido: {endereco}")
            return None
        
        rua = partes[0].strip()
        numero_raw = partes[1].strip().split()[0]
        
        logger.info(f"[IPTU] Buscando: '{rua}' {numero_raw}")
        
        # Tentar com número original
        chave1 = f"{rua}, {numero_raw}"
        if chave1 in IPTU_DATABASE:
            resultado = IPTU_DATABASE[chave1]
            logger.info(f"[IPTU] ✅ Encontrado: {resultado['metragem']} m²")
            return resultado
        
        # Tentar com padding de zeros
        if numero_raw.isdigit():
            numero_padded = numero_raw.zfill(5)
            chave2 = f"{rua}, {numero_padded}"
            if chave2 in IPTU_DATABASE:
                resultado = IPTU_DATABASE[chave2]
                logger.info(f"[IPTU] ✅ Encontrado (com padding): {resultado['metragem']} m²")
                return resultado
        
        logger.warning(f"[IPTU] Não encontrado: {rua}, {numero_raw}")
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
        "version": "36.0",
        "iptu_count": len(IPTU_DATABASE)
    }), 200


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
        
        # Extrair endereço
        data = request.get_json(force=True, silent=True) or {}
        endereco = data.get('endereco', '').strip()
        
        logger.info(f"[IPTU] Endereço: '{endereco}'")
        
        if not endereco:
            logger.error(f"[IPTU] Endereço vazio")
            return jsonify({
                "metragem": None,
                "fonte": "erro",
                "mensagem": "Endereço não fornecido",
                "sucesso": False
            }), 400
        
        # Consultar IPTU
        resultado = consultar_iptu(endereco)
        
        if resultado:
            logger.info(f"[IPTU] ✅ Retornando: {resultado['metragem']} m²")
            return jsonify({
                "metragem": resultado['metragem'],
                "fonte": "iptu_local",
                "rua": resultado['rua'],
                "numero": resultado['numero'],
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
    """
    Envia imagem de satélite para o WhatsApp via WATI API
    """
    try:
        # Formatar telefone
        phone = telefone.replace(" ", "").replace("-", "")
        if not phone.startswith("+"):
            phone = "+" + phone
        
        logger.info(f"[SATELLITE] Passo 1: Obtendo imagem para {endereco}")
        
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
        
        # Enviar a imagem via WATI API
        logger.info(f"[SATELLITE] Passo 2: Enviando para {phone}")
        
        headers = {"Authorization": f"Bearer {WATI_API_TOKEN}"}
        url_session = f"{WATI_BASE_URL}/{WATI_TENANT_ID}/api/v1/sendSessionFile/{phone}"
        
        logger.info(f"[SATELLITE] URL: {url_session}")
        
        files = {'file': ('satellite.png', io.BytesIO(response_img.content), 'image/png')}
        data = {'caption': f'Imagem de satélite: {endereco}'}
        
        response_session = requests.post(url_session, headers=headers, files=files, data=data, timeout=30)
        
        logger.info(f"[SATELLITE] Resposta: {response_session.status_code}")
        
        if response_session.status_code in [200, 201]:
            logger.info(f"[SATELLITE] ✅ Imagem enviada!")
            return True
        else:
            logger.warning(f"[SATELLITE] ⚠️ Falha (status {response_session.status_code})")
            return False
        
    except Exception as e:
        logger.error(f"[SATELLITE] Erro: {str(e)}")
        return False


@app.route('/analise-imagemdesatelite', methods=['POST'])
def analise_imagemdesatelite():
    """
    Endpoint para enviar imagem de satélite
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
                "endereco": endereco,
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
    logger.info("[MAIN] SITKA Webhook v36.0 iniciado")
    logger.info(f"[MAIN] {len(IPTU_DATABASE)} endereços cadastrados no banco local")
    
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
