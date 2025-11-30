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
import pandas as pd

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
# CARREGAMENTO DE DADOS IPTU
# ============================================================================

IPTU_DF = None

def carregar_iptu_csv():
    """
    Carrega arquivo IPTU_2025_OTIMIZADO.csv em memória (uma única vez)
    """
    global IPTU_DF
    
    try:
        iptu_file = '/app/IPTU_2025_OTIMIZADO.csv'
        
        # Verificar se arquivo existe
        if not os.path.exists(iptu_file):
            logger.warning(f"[IPTU_CSV] Arquivo não encontrado: {iptu_file}")
            return False
        
        logger.info(f"[IPTU_CSV] Carregando arquivo...")
        
        # Ler CSV com encoding latin-1
        IPTU_DF = pd.read_csv(
            iptu_file,
            sep=';',
            encoding='latin-1',
            dtype={
                'NUMERO DO IMOVEL': str,
                'AREA DO TERRENO': 'float64',
                'AREA CONSTRUIDA': 'float64'
            }
        )
        
        # Limpar nomes de colunas
        IPTU_DF.columns = [col.strip().upper() for col in IPTU_DF.columns]
        
        # Limpar dados
        IPTU_DF['NOME DE LOGRADOURO DO IMOVEL'] = IPTU_DF['NOME DE LOGRADOURO DO IMOVEL'].str.strip().str.upper()
        IPTU_DF['NUMERO DO IMOVEL'] = IPTU_DF['NUMERO DO IMOVEL'].astype(str).str.strip()
        
        logger.info(f"[IPTU_CSV] ✅ {len(IPTU_DF):,} linhas carregadas em {(os.path.getsize(iptu_file) / (1024**2)):.1f} MB")
        return True
        
    except Exception as e:
        logger.error(f"[IPTU_CSV] Erro ao carregar: {str(e)}")
        return False


def consultar_iptu_csv(endereco):
    """
    Consulta IPTU no DataFrame carregado em memória
    """
    global IPTU_DF
    
    if IPTU_DF is None or IPTU_DF.empty:
        logger.warning("[IPTU_CSV] DataFrame vazio ou não carregado")
        return None
    
    try:
        # Limpar endereço
        endereco_limpo = endereco.strip().upper()
        
        # Extrair rua e número
        partes = endereco_limpo.split(',')
        
        if len(partes) < 2:
            logger.warning(f"[IPTU_CSV] Formato inválido: {endereco}")
            return None
        
        rua = partes[0].strip()
        numero = partes[1].strip().split()[0]  # Pega primeiro token após vírgula
        
        logger.info(f"[IPTU_CSV] Buscando: Rua='{rua}', Número='{numero}'")
        
        # Buscar no DataFrame
        resultado = IPTU_DF[
            (IPTU_DF['NOME DE LOGRADOURO DO IMOVEL'].str.contains(rua, na=False, regex=False)) &
            (IPTU_DF['NUMERO DO IMOVEL'] == numero)
        ]
        
        if not resultado.empty:
            linha = resultado.iloc[0]
            metragem = linha['AREA DO TERRENO']
            
            if pd.notna(metragem) and metragem > 0:
                logger.info(f"[IPTU_CSV] ✅ Encontrado: {metragem} m²")
                return {
                    "metragem": float(metragem),
                    "endereco": linha['NOME DE LOGRADOURO DO IMOVEL'],
                    "numero": linha['NUMERO DO IMOVEL'],
                    "bairro": linha['BAIRRO DO IMOVEL']
                }
        
        logger.warning(f"[IPTU_CSV] Não encontrado: {rua}, {numero}")
        return None
        
    except Exception as e:
        logger.error(f"[IPTU_CSV] Erro: {str(e)}")
        return None


# ============================================================================
# BANCO DE DADOS LOCAL DE IPTU (FALLBACK)
# ============================================================================

IPTU_DATABASE = {
    "av paulista, 1000": {"metragem": 2500, "endereco": "Avenida Paulista, 1000", "sql": "SP001"},
    "avenida paulista, 1000": {"metragem": 2500, "endereco": "Avenida Paulista, 1000", "sql": "SP001"},
}

# ============================================================================
# HEALTH CHECK
# ============================================================================

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "service": "SITKA Webhook",
        "status": "ok",
        "version": "29.0",
        "iptu_loaded": IPTU_DF is not None and len(IPTU_DF) > 0
    }), 200

# ============================================================================
# FUNÇÕES DE PARSING
# ============================================================================

def extrair_endereco_do_body():
    """
    Extrai endereço do body com múltiplas estratégias
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
    Usa CSV IPTU como fonte principal
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
        
        # ESTRATÉGIA 1: Consultar CSV IPTU
        logger.info(f"[IPTU] Estratégia 1: CSV IPTU")
        resultado_csv = consultar_iptu_csv(endereco)
        
        if resultado_csv and resultado_csv.get('metragem'):
            logger.info(f"[IPTU] ✅ CSV: {resultado_csv['metragem']} m²")
            return jsonify({
                "metragem": resultado_csv['metragem'],
                "fonte": "iptu_csv",
                "endereco": resultado_csv.get('endereco'),
                "numero": resultado_csv.get('numero'),
                "bairro": resultado_csv.get('bairro'),
                "sucesso": True
            }), 200
        
        # ESTRATÉGIA 2: Consultar banco local (fallback)
        logger.info(f"[IPTU] Estratégia 2: Banco local")
        endereco_limpo = endereco.lower().strip()
        
        if endereco_limpo in IPTU_DATABASE:
            resultado_local = IPTU_DATABASE[endereco_limpo]
            logger.info(f"[IPTU] ✅ Banco local: {resultado_local['metragem']} m²")
            return jsonify({
                "metragem": resultado_local['metragem'],
                "fonte": "iptu_local",
                "endereco": resultado_local.get('endereco'),
                "sql": resultado_local.get('sql'),
                "sucesso": True
            }), 200
        
        logger.warning(f"[IPTU] Não encontrado em nenhuma fonte: {endereco}")
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
    Envia imagem de satélite para o WhatsApp via WATI API (sendSessionFile)
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
# STARTUP
# ============================================================================

@app.before_request
def startup():
    """Executado antes da primeira requisição"""
    global IPTU_DF
    
    if IPTU_DF is None:
        logger.info("[STARTUP] Carregando dados IPTU...")
        carregar_iptu_csv()


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    # Carregar IPTU na inicialização
    logger.info("[MAIN] Carregando dados IPTU...")
    carregar_iptu_csv()
    
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
