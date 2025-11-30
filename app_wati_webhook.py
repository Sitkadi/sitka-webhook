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
# BANCO DE DADOS LOCAL DE IPTU (SEM PANDAS)
# ============================================================================

# Carregado em memória na inicialização
IPTU_DATABASE = {}

def carregar_iptu_local():
    """
    Carrega dados IPTU do CSV em um dicionário simples
    Sem usar pandas - apenas leitura de arquivo
    """
    global IPTU_DATABASE
    
    try:
        # Procurar em múltiplos caminhos
        caminhos_possiveis = [
            '/app/IPTU_2025_OTIMIZADO.csv',
            'IPTU_2025_OTIMIZADO.csv',
            '/opt/render/project/src/IPTU_2025_OTIMIZADO.csv'
        ]
        
        iptu_file = None
        for caminho in caminhos_possiveis:
            if os.path.exists(caminho):
                iptu_file = caminho
                logger.info(f"[IPTU] Arquivo encontrado: {iptu_file}")
                break
        
        if not iptu_file:
            logger.warning(f"[IPTU] Arquivo não encontrado em nenhum caminho")
            return False
        
        logger.info(f"[IPTU] Carregando arquivo...")
        
        count = 0
        with open(iptu_file, 'r', encoding='latin-1') as f:
            # Pular header
            header = f.readline().strip().split(';')
            
            # Encontrar índices das colunas
            idx_rua = header.index('NOME DE LOGRADOURO DO IMOVEL')
            idx_numero = header.index('NUMERO DO IMOVEL')
            idx_metragem = header.index('AREA DO TERRENO')
            
            # Ler linhas
            for line in f:
                partes = line.strip().split(';')
                
                if len(partes) <= max(idx_rua, idx_numero, idx_metragem):
                    continue
                
                rua = partes[idx_rua].strip().upper()
                numero = partes[idx_numero].strip()
                metragem_str = partes[idx_metragem].strip()
                
                try:
                    metragem = float(metragem_str.replace(',', '.'))
                except:
                    continue
                
                # Chave: "RUA, NUMERO"
                chave = f"{rua}, {numero}"
                IPTU_DATABASE[chave] = {
                    "metragem": metragem,
                    "rua": rua,
                    "numero": numero
                }
                
                count += 1
                if count % 100000 == 0:
                    logger.info(f"[IPTU] {count:,} registros carregados...")
        
        logger.info(f"[IPTU] ✅ {count:,} registros carregados com sucesso!")
        return True
        
    except Exception as e:
        logger.error(f"[IPTU] Erro ao carregar: {str(e)}")
        return False


def consultar_iptu(endereco):
    """
    Consulta IPTU no banco de dados local
    Tenta múltiplos formatos
    """
    if not IPTU_DATABASE:
        logger.warning("[IPTU] Banco de dados vazio")
        return None
    
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
        "version": "34.0",
        "iptu_loaded": len(IPTU_DATABASE) > 0,
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
    global IPTU_DATABASE
    
    if not IPTU_DATABASE:
        logger.info("[STARTUP] Carregando dados IPTU...")
        carregar_iptu_local()


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    # Carregar IPTU na inicialização
    logger.info("[MAIN] Carregando dados IPTU...")
    carregar_iptu_local()
    
    port = int(os.getenv('PORT', 10000))
    app.run(host='0.0.0.0', port=port, debug=False)
