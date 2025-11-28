#!/usr/bin/env python3
"""
SITKA - API Flask para Webhook WATI
Endpoint: /analise-imagemdesatelite
Função: Gerar imagem de satélite e enviar via WhatsApp
Data: 28/11/2025
"""

from flask import Flask, request, jsonify
import requests
import logging
import os
import tempfile
from dotenv import load_dotenv
from typing import Optional, Dict, Tuple

# Carregar variáveis de ambiente
load_dotenv()

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Criar aplicação Flask
app = Flask(__name__)

# Configurações WATI
WATI_TOKEN = os.getenv("WATI_API_TOKEN", "")
WATI_TENANT_ID = os.getenv("WATI_TENANT_ID", "1047617")
WATI_BASE_URL = os.getenv("WATI_BASE_URL", "https://live-mt-server.wati.io")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")


class SatelliteImageService:
    """Serviço para gerar imagens de satélite e enviar via WATI."""
    
    def __init__(self):
        self.google_api_key = GOOGLE_API_KEY
        self.wati_token = WATI_TOKEN
        self.wati_tenant_id = WATI_TENANT_ID
        self.wati_base_url = WATI_BASE_URL
    
    def formatar_numero(self, phone_number: str) -> str:
        """Formata número de telefone para padrão WATI."""
        phone = str(phone_number).replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        
        if phone.startswith("+"):
            return phone
        if phone.startswith("55"):
            return "+" + phone
        if len(phone) == 11:
            return "+55" + phone
        if len(phone) == 10:
            return "+55" + phone
        
        return "+55" + phone
    
    def get_satellite_image(self, address: str, zoom: int = 18) -> Optional[str]:
        """
        Obtém imagem de satélite do Google Maps.
        
        Args:
            address: Endereço para buscar
            zoom: Nível de zoom (15-25)
            
        Returns:
            Caminho do arquivo de imagem temporário ou None
        """
        try:
            logger.info(f"[IMAGEM] Obtendo imagem: {address} (zoom: {zoom})")
            
            url = "https://maps.googleapis.com/maps/api/staticmap"
            params = {
                "center": address,
                "zoom": zoom,
                "size": "600x600",
                "maptype": "satellite",
                "markers": f"color:red|{address}",
                "key": self.google_api_key
            }
            
            response = requests.get(url, params=params, timeout=30)
            logger.info(f"[IMAGEM] Google Maps Response: {response.status_code}")
            response.raise_for_status()
            
            if response.headers.get('content-type', '').startswith('image'):
                temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                temp_file.write(response.content)
                temp_file.close()
                logger.info(f"[IMAGEM] Imagem salva em: {temp_file.name}")
                return temp_file.name
            
            logger.error("[IMAGEM] Resposta não é uma imagem")
            return None
            
        except Exception as e:
            logger.error(f"[IMAGEM] Erro ao obter imagem: {str(e)}")
            return None
    
    def send_via_wati(self, phone_number: str, address: str, image_path: str) -> Tuple[bool, Dict]:
        """
        Envia imagem via WATI API.
        
        Args:
            phone_number: Número de telefone
            address: Endereço para legenda
            image_path: Caminho da imagem
            
        Returns:
            Tupla (sucesso, resposta_json)
        """
        phone = self.formatar_numero(phone_number)
        
        try:
            logger.info(f"[WATI] Enviando para: {phone}")
            
            if not os.path.exists(image_path):
                logger.error(f"[WATI] Arquivo não encontrado: {image_path}")
                return False, {"error": "Arquivo de imagem não encontrado"}
            
            headers = {
                "Authorization": f"Bearer {self.wati_token}"
            }
            
            url = f"{self.wati_base_url}/{self.wati_tenant_id}/api/v1/sendSessionFile/{phone}"
            
            with open(image_path, 'rb') as img_file:
                files = {'file': ('satellite.png', img_file, 'image/png')}
                data = {'caption': f'Imagem de satélite para: {address}'}
                
                response = requests.post(
                    url,
                    headers=headers,
                    files=files,
                    data=data,
                    timeout=30
                )
                
                logger.info(f"[WATI] Status: {response.status_code}")
                logger.info(f"[WATI] Resposta: {response.text}")
                
                try:
                    resp_json = response.json()
                    if resp_json.get("result") == True:
                        logger.info("[WATI] OK - Imagem enviada com sucesso!")
                        return True, resp_json
                    else:
                        logger.error(f"[WATI] Erro: {resp_json.get('info', 'Desconhecido')}")
                        return False, resp_json
                except:
                    if response.status_code in [200, 201]:
                        logger.info("[WATI] OK - Imagem enviada com sucesso!")
                        return True, {"status": "success"}
                    else:
                        logger.error(f"[WATI] Erro: Status {response.status_code}")
                        return False, {"error": f"Status {response.status_code}"}
        
        except Exception as e:
            logger.error(f"[WATI] Erro ao enviar: {str(e)}")
            return False, {"error": str(e)}
    
    def process(self, phone_number: str, address: str, zoom: int = 18) -> Tuple[bool, Dict]:
        """
        Processa o envio completo: gera imagem e envia via WATI.
        
        Args:
            phone_number: Número de telefone
            address: Endereço
            zoom: Nível de zoom
            
        Returns:
            Tupla (sucesso, resposta)
        """
        temp_image_path = None
        
        try:
            # Passo 1: Obter imagem
            logger.info("[PROCESSO] Passo 1: Obtendo imagem")
            temp_image_path = self.get_satellite_image(address, zoom=zoom)
            
            if not temp_image_path:
                logger.error("[PROCESSO] Falha ao obter imagem")
                return False, {"error": "Falha ao obter imagem de satélite"}
            
            # Passo 2: Enviar via WATI
            logger.info("[PROCESSO] Passo 2: Enviando via WATI")
            success, response = self.send_via_wati(phone_number, address, temp_image_path)
            
            return success, response
        
        except Exception as e:
            logger.error(f"[PROCESSO] Erro geral: {str(e)}")
            return False, {"error": str(e)}
        
        finally:
            # Limpar arquivo temporário
            if temp_image_path and os.path.exists(temp_image_path):
                try:
                    os.remove(temp_image_path)
                    logger.info("[PROCESSO] Arquivo temporário removido")
                except Exception as e:
                    logger.warning(f"[PROCESSO] Não foi possível remover arquivo: {e}")


# ============================================================================
# ROTAS
# ============================================================================

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "service": "SITKA Webhook"}), 200


@app.route('/analise-imagemdesatelite', methods=['POST'])
def analise_imagem_desatelite():
    """
    Endpoint principal para análise de imagem de satélite.
    
    Recebe:
    {
        "telefone": "+5511987654321",
        "endereco": "Av. Paulista, 1000, São Paulo"
    }
    
    Retorna:
    {
        "success": true,
        "message": "Imagem enviada com sucesso",
        "data": {...}
    }
    """
    try:
        # Validar request
        if not request.is_json:
            logger.error("[ENDPOINT] Request não é JSON")
            return jsonify({
                "success": False,
                "error": "Content-Type deve ser application/json"
            }), 400
        
        data = request.get_json()
        
        # Extrair parâmetros
        telefone = data.get("telefone", "")
        endereco = data.get("endereco", "")
        zoom = data.get("zoom", 18)
        
        # Validar parâmetros
        if not telefone or not endereco:
            logger.error("[ENDPOINT] Parâmetros faltando")
            return jsonify({
                "success": False,
                "error": "Parâmetros 'telefone' e 'endereco' são obrigatórios"
            }), 400
        
        logger.info(f"[ENDPOINT] Recebido: telefone={telefone}, endereco={endereco}, zoom={zoom}")
        
        # Processar
        service = SatelliteImageService()
        success, response = service.process(telefone, endereco, zoom=zoom)
        
        if success:
            logger.info("[ENDPOINT] Sucesso!")
            return jsonify({
                "success": True,
                "message": "Imagem enviada com sucesso",
                "data": response
            }), 200
        else:
            logger.error(f"[ENDPOINT] Falha: {response}")
            return jsonify({
                "success": False,
                "error": response.get("error", "Erro desconhecido"),
                "data": response
            }), 400
    
    except Exception as e:
        logger.error(f"[ENDPOINT] Erro geral: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/analise-imagemdesatelite-test', methods=['GET'])
def analise_imagem_desatelite_test():
    """Endpoint de teste (GET)."""
    return jsonify({
        "status": "ok",
        "message": "Use POST com JSON",
        "example": {
            "telefone": "+5511987654321",
            "endereco": "Av. Paulista, 1000, São Paulo",
            "zoom": 18
        }
    }), 200


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    logger.info(f"Iniciando servidor na porta {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
