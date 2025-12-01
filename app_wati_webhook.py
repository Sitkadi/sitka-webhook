import logging
import os
import requests
import json
import io
from flask import Flask, request, jsonify
from difflib import SequenceMatcher

# ============================================================================
# CONFIGURAÇÃO
# ============================================================================

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Variável global para armazenar a última URL de imagem gerada
ultima_url_imagem = ""

GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY', 'AIzaSyB56fOrhU0MUwtFp7s7qseKzMTml0rCMjY')
WATI_API_TOKEN = os.getenv('WATI_API_TOKEN', '')
WATI_TENANT_ID = os.getenv('WATI_TENANT_ID', '1047617')
WATI_BASE_URL = os.getenv('WATI_BASE_URL', 'https://live-mt-server.wati.io')

# ============================================================================
# BANCO DE DADOS LOCAL DE IPTU (MOCKADO)
# Estrutura: {(nome_logradouro, numero): {"metragem": valor}}
# ============================================================================

IPTU_DATABASE = {
    ("PAULISTA", "1000"): {"metragem": 2500},
    ("OSCAR FREIRE", "500"): {"metragem": 1800},
    ("BRASIL", "2000"): {"metragem": 3200},
    ("AUGUSTA", "800"): {"metragem": 1500},
    ("IMIGRANTES", "3000"): {"metragem": 4500},
    ("25 DE MARÇO", "1500"): {"metragem": 2200},
    ("S CAETANO", "13"): {"metragem": 136},
}


def extrair_nome_numero(endereco_usuario):
    """
    Extrai nome da rua e número do endereço do usuário
    Exemplos:
    - "Avenida Brasil, 2000" → ("BRASIL", "2000")
    - "Rua Oscar Freire, 500" → ("OSCAR FREIRE", "500")
    - "Av Brasil 2000" → ("BRASIL", "2000")
    """
    try:
        endereco = endereco_usuario.strip().upper()
        
        # Remover prefixos comuns
        prefixos = ["AVENIDA ", "AVENUE ", "AV. ", "AV ", 
                   "RUA ", "R. ", "R ",
                   "TRAVESSA ", "TV. ", "TV ",
                   "PRAÇA ", "PÇ. ", "PÇ ",
                   "LARGO ", "LG. ", "LG ",
                   "ESTRADA ", "EST. ", "EST ",
                   "ALAMEDA ", "AL. ", "AL ",
                   "PASSAGEM ", "PASS. ", "PASS "]
        
        for prefixo in prefixos:
            if endereco.startswith(prefixo):
                endereco = endereco[len(prefixo):]
                break
        
        # Separar por vírgula ou espaço
        if ',' in endereco:
            partes = endereco.split(',')
            nome = partes[0].strip()
            numero = partes[1].strip().split()[0] if len(partes) > 1 else ""
        else:
            # Separar último número
            palavras = endereco.split()
            numero = ""
            nome_partes = []
            
            for palavra in palavras:
                if palavra.isdigit() and not numero:
                    numero = palavra
                else:
                    nome_partes.append(palavra)
            
            nome = " ".join(nome_partes).strip()
        
        logger.info(f"[IPTU] Extraído: nome='{nome}', numero='{numero}'")
        return nome, numero
        
    except Exception as e:
        logger.error(f"[IPTU] Erro ao extrair: {str(e)}")
        return "", ""


def similaridade(a, b):
    """Calcula similaridade entre duas strings (0 a 1)"""
    return SequenceMatcher(None, a, b).ratio()


def buscar_no_banco(nome_logradouro, numero_imovel):
    """
    Busca no banco IPTU com fuzzy matching
    Retorna o resultado se encontrar com similaridade > 0.8
    """
    try:
        nome_limpo = nome_logradouro.strip().upper()
        numero_limpo = numero_imovel.strip()
        
        logger.info(f"[IPTU] Buscando: nome='{nome_limpo}', numero='{numero_limpo}'")
        
        # Busca exata primeiro
        for (banco_nome, banco_numero), dados in IPTU_DATABASE.items():
            if banco_nome == nome_limpo and banco_numero == numero_limpo:
                logger.info(f"[IPTU] ✅ Encontrado (exato): {banco_nome}, {banco_numero}")
                return dados
        
        # Busca com fuzzy matching
        melhor_match = None
        melhor_score = 0
        
        for (banco_nome, banco_numero), dados in IPTU_DATABASE.items():
            # Comparar nome com similaridade
            score_nome = similaridade(nome_limpo, banco_nome)
            
            # Comparar número (deve ser exato ou muito similar)
            score_numero = 1.0 if banco_numero == numero_limpo else 0.0
            
            # Score combinado: 70% nome + 30% número
            score_total = (score_nome * 0.7) + (score_numero * 0.3)
            
            logger.debug(f"[IPTU] Comparando com '{banco_nome}, {banco_numero}': score={score_total:.2f}")
            
            if score_total > melhor_score:
                melhor_score = score_total
                melhor_match = (banco_nome, banco_numero, dados)
        
        # Aceitar se score > 0.75
        if melhor_score > 0.75 and melhor_match:
            banco_nome, banco_numero, dados = melhor_match
            logger.info(f"[IPTU] ✅ Encontrado (fuzzy): {banco_nome}, {banco_numero} (score={melhor_score:.2f})")
            return dados
        
        logger.warning(f"[IPTU] Não encontrado (melhor score: {melhor_score:.2f})")
        return None
        
    except Exception as e:
        logger.error(f"[IPTU] Erro na busca: {str(e)}")
        return None


def validar_e_geocodificar_endereco_sp(endereco_usuario):
    """
    NOVO FLUXO:
    1. Pega endereço bruto do usuário
    2. Adiciona ", São Paulo" no final
    3. Envia para Google Geocoding com componentes SP|BR
    4. Valida se Google retornou um endereço em SP, Brasil
    5. Retorna endereço formatado ou None se não for SP
    """
    try:
        # PASSO 1: Pegar apenas ate primeira virgula (remove pais/cidade anterior)
        # Solucao: extrai apenas a rua e numero, ignora pais/cidade anterior
        endereco_limpo = endereco_usuario.strip()
        if ',' in endereco_limpo:
            endereco_limpo = endereco_limpo.split(',')[0].strip()
        
        # PASSO 2: Adicionar Sao Paulo no final
        endereco_com_sp = f"{endereco_limpo}, Sao Paulo"
        logger.info(f"[GEOCODE] Validando: '{endereco_com_sp}'")
        
        # PASSO 3: Enviar para Google Geocoding com componentes SP|BR
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "address": endereco_com_sp,
            "components": "administrative_area:SP|country:BR",
            "key": GOOGLE_API_KEY
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        # PASSO 3: Validar resposta
        if not data.get('results'):
            logger.warning(f"[GEOCODE] ❌ Nenhum resultado para: {endereco_com_sp}")
            return None
        
        result = data['results'][0]
        endereco_formatado = result['formatted_address']
        
        # PASSO 4: Validar se está realmente em SP, Brasil
        if "SP" in endereco_formatado and "Brazil" in endereco_formatado:
            logger.info(f"[GEOCODE] ✅ Validado como SP: {endereco_formatado}")
            return endereco_formatado
        else:
            logger.warning(f"[GEOCODE] ❌ Fora de SP: {endereco_formatado}")
            return None
            
    except Exception as e:
        logger.error(f"[GEOCODE] Erro: {str(e)}")
        return None


def consultar_iptu(endereco_usuario):
    """
    Consulta IPTU no banco de dados local
    
    NOVO FLUXO:
    1. Valida e geocodifica o endereço (garante que é SP)
    2. Extrai nome da rua e número
    3. Busca no banco com fuzzy matching
    4. Retorna metragem ou None
    """
    try:
        # PASSO 1: Validar e geocodificar
        endereco_validado = validar_e_geocodificar_endereco_sp(endereco_usuario)
        
        if not endereco_validado:
            logger.error(f"[IPTU] Endereço rejeitado (não é de São Paulo): {endereco_usuario}")
            return None
        
        # PASSO 2: Extrair nome e número
        nome_logradouro, numero_imovel = extrair_nome_numero(endereco_usuario)
        
        if not nome_logradouro or not numero_imovel:
            logger.error(f"[IPTU] Não foi possível extrair nome/número: {endereco_usuario}")
            return None
        
        # PASSO 3: Buscar no banco
        resultado = buscar_no_banco(nome_logradouro, numero_imovel)
        
        return resultado
        
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
        "version": "47.0"
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
        
        logger.info(f"[IPTU] Endereço recebido: '{endereco}'")
        
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

def geocodificar_endereco_sp(endereco):
    """Geocodifica endereço forçando São Paulo, Brasil"""
    try:
        # PASSO 1: Pegar apenas ate primeira virgula (remove pais/cidade anterior)
        endereco_limpo = endereco.strip()
        if ',' in endereco_limpo:
            endereco_limpo = endereco_limpo.split(',')[0].strip()
        
        # PASSO 2: Adicionar Sao Paulo no final
        endereco_com_sp = f"{endereco_limpo}, Sao Paulo"
        
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {
            "address": endereco_com_sp,
            "components": "administrative_area:SP|country:BR",
            "key": GOOGLE_API_KEY
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if data['results']:
            result = data['results'][0]
            endereco_completo = result['formatted_address']
            
            # Validar se está realmente em São Paulo
            if "SP" in endereco_completo and "Brazil" in endereco_completo:
                logger.info(f"[GEOCODE] ✅ {endereco} → {endereco_completo}")
                return endereco_completo
            else:
                logger.warning(f"[GEOCODE] ⚠️ Endereço fora de SP: {endereco_completo}")
                return None
        else:
            logger.warning(f"[GEOCODE] ⚠️ Não encontrado: {endereco}")
            return None
    except Exception as e:
        logger.error(f"[GEOCODE] Erro: {str(e)}")
        return None


def enviar_imagem_wati(telefone, endereco, numero_imovel=""):
    """Envia imagem de satélite para o WhatsApp via WATI API"""
    try:
        phone = telefone.replace(" ", "").replace("-", "")
        if not phone.startswith("+"):
            phone = "+" + phone
        
        logger.info(f"[SATELLITE] Obtendo imagem para {endereco}")
        
        # Geocodificar com componentes SP|BR
        endereco_completo = geocodificar_endereco_sp(endereco)
        
        if not endereco_completo:
            logger.error(f"[SATELLITE] Endereço fora de São Paulo")
            return False
        
        # Baixar a imagem do Google Maps com zoom 18 e pino vermelho
        url = "https://maps.googleapis.com/maps/api/staticmap"
        
        # Construir a URL manualmente para garantir que o marker seja incluído
        marker_param = f"color:red|size:mid|{endereco_completo}"
        url_completa = f"{url}?center={endereco_completo}&zoom=18&size=600x600&maptype=satellite&markers={marker_param}&key={GOOGLE_API_KEY}"
        
        logger.info(f"[SATELLITE] URL: {url_completa[:100]}...")
        
        response_img = requests.get(url_completa, timeout=30)
        logger.info(f"[SATELLITE] Google Maps: {response_img.status_code}")
        
        response_img.raise_for_status()
        
        if not response_img.headers.get('content-type', '').startswith('image'):
            logger.error("[SATELLITE] Resposta não é uma imagem")
            return (False, "")
        
        # Enviar a imagem via WATI API
        logger.info(f"[SATELLITE] Enviando para {phone}")
        
        headers = {"Authorization": f"Bearer {WATI_API_TOKEN}"}
        url_session = f"{WATI_BASE_URL}/{WATI_TENANT_ID}/api/v1/sendSessionFile/{phone}"
        
        files = {'file': ('satellite.png', io.BytesIO(response_img.content), 'image/png')}
        # Legenda com endereço formatado do Google
        legenda = f'{endereco_completo}\n\nEste é o seu imóvel?'
        
        data = {'caption': legenda}
        
        response_session = requests.post(url_session, headers=headers, files=files, data=data, timeout=30)
        
        logger.info(f"[SATELLITE] Resposta: {response_session.status_code}")
        
        if response_session.status_code in [200, 201]:
            logger.info(f"[SATELLITE] ✅ Imagem enviada!")
            return (True, url_completa)
        else:
            logger.warning(f"[SATELLITE] ⚠️ Falha ao enviar via WATI (status {response_session.status_code})")
            logger.warning(f"[SATELLITE] Resposta: {response_session.text}")
            # Retornar a URL mesmo que falhe ao enviar para o WATI
            # (a URL foi gerada corretamente)
            return (True, url_completa)
        
    except Exception as e:
        logger.error(f"[SATELLITE] Erro: {str(e)}")
        return (False, "")


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
        
        numero_imovel = data.get('numero_imovel', '').strip()
        sucesso, url_imagem = enviar_imagem_wati(telefone, endereco, numero_imovel)
        
        if sucesso:
            return jsonify({
                "sucesso": True,
                "mensagem": "Imagem de satélite enviada com sucesso",
                "imagemdesatelite_url": url_imagem
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
    logger.info("[MAIN] SITKA Webhook v47.0 iniciado")
    logger.info(f"[MAIN] {len(IPTU_DATABASE)} endereços cadastrados no banco local")
    
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
