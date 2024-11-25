import time 
import board
import busio
from datetime import datetime
from typing import Tuple, Optional
from adafruit_pn532.i2c import PN532_I2C
import RPi.GPIO as GPIO
import requests
from threading import Thread
from flask import Flask, jsonify

class HardwareController:
    """하드웨어 제어 클래스: LED 및 부저 제어 담당"""
    
    def __init__(self, green_led_pin=15, red_led_pin=14, buzzer_pin=10):
        # GPIO 초기화
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        
        self.pins = {
            'green_led': green_led_pin,
            'red_led': red_led_pin,
            'buzzer': buzzer_pin
        }
        
        # 모든 핀을 출력 모드로 설정
        for pin in self.pins.values():
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)
        
        self._blink_flag = False
    
    def _blink_led(self, led_pin: int, duration: float = 0.5):
        """LED 깜박임 제어"""
        GPIO.output(led_pin, GPIO.HIGH)
        time.sleep(duration)
        GPIO.output(led_pin, GPIO.LOW)
    
    def _beep(self, duration: float = 0.2):
        """부저 울림"""
        GPIO.output(self.pins['buzzer'], GPIO.HIGH)
        time.sleep(duration)
        GPIO.output(self.pins['buzzer'], GPIO.LOW)

    def indicate_success(self):
        """성공 표시: 녹색 LED 켜짐 + 부저 1회 울림"""
        self._beep(0.1)
        self._blink_led(self.pins['green_led'], 2)

    def indicate_failure(self):
        """실패 표시: 빨간 LED 깜박임 + 부저 2회 울림"""
        for _ in range(2):
            self._beep(0.1)
            self._blink_led(self.pins['red_led'], 0.1)
            time.sleep(0.1)

    def start_enrollment_indicator(self):
        """등록 시작 표시: 녹색 LED 깜박임"""
        self._blink_led(self.pins['green_led'], 0.5)
    
    def cleanup(self):
        """GPIO 리소스 정리"""
        GPIO.cleanup()

class PN532Handler:
    """RFID 리더기 메인 제어 클래스"""
    
    def __init__(self, device_mode: int, retry_count: int = 3):
        self.device_mode = device_mode
        self.retry_count = retry_count
        # 하드웨어 컨트롤러 초기화
        self.hw = HardwareController()
        self._initialize_pn532()
    
    def _initialize_pn532(self):
        """PN532 초기화, 재시도 메커니즘 포함"""
        for attempt in range(self.retry_count):
            try:
                i2c = busio.I2C(board.SCL, board.SDA)
                time.sleep(1)
                self.pn532 = PN532_I2C(i2c, debug=False)
                self.pn532.SAM_configuration()
                version = self.pn532.firmware_version
                print(f"PN532 펌웨어 버전 확인됨: {version}")
                return True
            except Exception as e:
                print(f"초기화 시도 {attempt + 1} 실패: {str(e)}")
                if attempt < self.retry_count - 1:
                    time.sleep(2)
                else:
                    raise RuntimeError("PN532 초기화 실패. 하드웨어 연결을 확인하세요.")

    def read_card(self, timeout: float = 1) -> Optional[str]:
        """카드 UID 읽기"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                uid = self.pn532.read_passive_target(timeout=0.5)
                if uid is not None:
                    return bytes(uid).hex()
            except Exception as e:
                print(f"카드 읽기 오류: {str(e)}")
                time.sleep(0.1)
        return None

    def check_card_access(self):
        """리더기 모드: 카드 읽기 및 권한 검증 지속"""
        if self.device_mode != READER_MODE:
            raise RuntimeError("현재 장치는 리더기 모드가 아닙니다.")
            
        print("\n카드 접근 검증 모드 시작... Ctrl+C로 종료.")
        try:
            while True:
                card_id = self.read_card()
                if card_id is None:
                    continue
                
                try:
                    response = requests.get(
                        f"{API_BASE_URL}/api/entry/{card_id}", # Check Here
                        timeout=REQUEST_TIMEOUT
                    )
                    if response.status_code == 200 and response.json().get('allowed'):
                        print(f"환영합니다, 카드 ID: {card_id}")
                        self.hw.indicate_success()
                    else:
                        print(f"경고! 미승인 카드 ID: {card_id}")
                        self.hw.indicate_failure()
                except requests.RequestException as e:
                    print(f"서버 연결 실패: {str(e)}")
                    self.hw.indicate_failure()
                
                time.sleep(0.5)
                
        except KeyboardInterrupt:
            print("\n카드 검증 모드 종료.")
        except Exception as e:
            print(f"카드 검증 모드 오류: {str(e)}")

    def start_enrollment_server(self, port: int = 5000):
        """등록기 모드: Flask 서버 시작, 등록 명령 대기"""
        if self.device_mode != ENROLLER_MODE:
            raise RuntimeError("현재 장치는 등록기 모드가 아닙니다.")
            
        app = Flask(__name__)
        
        @app.route('/api', methods=['POST'])
        def enroll():
            try:
                self.hw.start_enrollment_indicator()  # 등록 시작 표시
                card_id = self.read_card(timeout=10)
                
                if card_id is None:
                    self.hw.indicate_failure()
                    return jsonify({'status': 'error', 'message': '카드 읽기 시간 초과'}), 408
                    
                response = requests.post(
                    f"{API_BASE_URL}/users/enroll", # Check Here
                    json={'card_id': card_id},
                    timeout=REQUEST_TIMEOUT
                )
                
                if response.status_code == 200:
                    self.hw.indicate_success()
                    return jsonify({
                        'type': 'rfid',
                        'card_id': card_id,
                        'status': 'success',
                        'message': '카드 등록 성공'
                    })
                else:
                    self.hw.indicate_failure()
                    return jsonify({
                        'status': 'error',
                        'message': '원격 서버 등록 실패'
                    }), 500
                    
            except Exception as e:
                self.hw.indicate_failure()
                return jsonify({
                    'status': 'error',
                    'message': str(e)
                }), 500

        Thread(target=lambda: app.run(host='0.0.0.0', port=port, debug=False)).start()
        print(f"등록 서버가 포트 {port}에서 시작되었습니다.")

    def __del__(self):
        """소멸자: 하드웨어 리소스 정리"""
        if hasattr(self, 'hw'):
            self.hw.cleanup()

# 상수 정의
READER_MODE = 0
ENROLLER_MODE = 1
API_BASE_URL = "http://your-remote-server:5000"
REQUEST_TIMEOUT = 5
CARD_READ_TIMEOUT = 1

DEVICE_MODE = READER_MODE # To Fulfill

if __name__ == "__main__":
    try:
        handler = PN532Handler(device_mode=DEVICE_MODE)

        if DEVICE_MODE == READER_MODE:
            handler.check_card_access()
        else:
            handler.start_enrollment_server()
            
    except Exception as e:
        print(f"프로그램 오류: {str(e)}")
