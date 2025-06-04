#include "esp_camera.h"
#include <WiFi.h>
#include "esp_http_server.h"
#include "esp_timer.h"
#include <HTTPClient.h>
#include <ArduinoJson.h>

#define CAMERA_MODEL_AI_THINKER
#include "camera_pins.h"

const char* ssid = "ESP32-CAM-AP";
const char* password = "esp32cam123";

static const char* STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=frame";
static const char* STREAM_BOUNDARY = "\r\n--frame\r\n";
static const char* STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

#define FLASH_LED_PIN 4
bool flashState = false;

httpd_handle_t camera_httpd = NULL;
httpd_handle_t stream_httpd = NULL;

// init camera, wifi ap & start servers
void setup() {
  // prevent brownout resets during camera ops & wifi startup
  //WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);
  
  pinMode(FLASH_LED_PIN, OUTPUT);
  digitalWrite(FLASH_LED_PIN, LOW); // flash off initially
  
  Serial.begin(115200);
  Serial.println("ESP32-CAM starting...");

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  
  // psram allows higher res + frame buffering for smooth streaming
  if(psramFound()){
    config.frame_size = FRAMESIZE_VGA;
    config.jpeg_quality = 10;
    config.fb_count = 2;
  } else {
    config.frame_size = FRAMESIZE_SVGA;
    config.jpeg_quality = 12;
    config.fb_count = 1;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x\n", err);
    ESP.restart(); // hard reset on camera fail
  }

  sensor_t * s = esp_camera_sensor_get();
  s->set_vflip(s, 1);       // flip image orientation
  s->set_hmirror(s, 1);

  WiFi.softAP(ssid, password);
  IPAddress IP = WiFi.softAPIP();
  
  Serial.println("WiFi AP started");
  Serial.printf("SSID: %s\n", ssid);
  Serial.printf("Password: %s\n", password);
  Serial.printf("IP: http://%s\n", IP.toString().c_str());

  startCameraServer();
  Serial.println("Camera server ready!");
}

// main loop - just keep alive
void loop() {
  delay(10000);
}

// setup both http servers with endpoints
void startCameraServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 80;

  httpd_uri_t routes[] = {
    {"/", HTTP_GET, index_handler, NULL},
    {"/capture", HTTP_GET, capture_handler, NULL},
    {"/flash", HTTP_GET, flash_handler, NULL},
    {"/status", HTTP_GET, status_handler, NULL}
  };

  if (httpd_start(&camera_httpd, &config) == ESP_OK) {
    for(int i = 0; i < 4; i++) {
      httpd_register_uri_handler(camera_httpd, &routes[i]);
    }
    Serial.println("Main server started on port 80");
  }

  // separate server for continuous streaming to avoid blocking main endpoints
  config.server_port = 81;
  config.ctrl_port = 81;
  if (httpd_start(&stream_httpd, &config) == ESP_OK) {
    httpd_uri_t stream_uri = {"/stream", HTTP_GET, stream_handler, NULL};
    httpd_register_uri_handler(stream_httpd, &stream_uri);
    Serial.println("Stream server started on port 81");
  }
}

// serve basic html interface
static esp_err_t index_handler(httpd_req_t *req) {
  httpd_resp_set_type(req, "text/html");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  const char* html = 
    "<html><head><title>ESP32-CAM</title></head><body>"
    "<h1>ESP32-CAM Server</h1>"
    "<p><a href='/stream'>Live Stream</a></p>"
    "<p><a href='/capture'>Capture Photo</a></p>"
    "<p><a href='/flash'>Toggle Flash</a></p>"
    "</body></html>";
  
  return httpd_resp_send(req, html, HTTPD_RESP_USE_STRLEN);
}

// take single photo with flash
static esp_err_t capture_handler(httpd_req_t *req) {
  digitalWrite(FLASH_LED_PIN, HIGH); // flash on for photo
  delay(100);
  
  camera_fb_t * fb = esp_camera_fb_get();
  digitalWrite(FLASH_LED_PIN, LOW);  // flash off
  
  if (!fb) {
    Serial.println("Camera capture failed");
    httpd_resp_send_500(req);
    return ESP_FAIL;
  }

  httpd_resp_set_type(req, "image/jpeg");
  httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.jpg");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  esp_err_t result = httpd_resp_send(req, (const char *)fb->buf, fb->len);
  esp_camera_fb_return(fb);
  
  return result;
}

// live video stream endpoint
static esp_err_t stream_handler(httpd_req_t *req) {
  camera_fb_t * fb = NULL;
  esp_err_t res = ESP_OK;
  char part_buf[64];

  httpd_resp_set_type(req, STREAM_CONTENT_TYPE);
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");

  // continuous mjpeg stream using multipart http response
  while(true) {
    fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("Camera capture failed");
      res = ESP_FAIL;
      break;
    }

    if(fb->format == PIXFORMAT_JPEG) {
      res = httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY));
      
      if(res == ESP_OK) {
        size_t hlen = snprintf(part_buf, 64, STREAM_PART, fb->len);
        res = httpd_resp_send_chunk(req, part_buf, hlen);
      }
      
      if(res == ESP_OK) {
        res = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
      }
    } else {
      Serial.println("Unsupported format");
      res = ESP_FAIL;
    }
    
    esp_camera_fb_return(fb);
    
    if(res != ESP_OK) break;
    
    vTaskDelay(pdMS_TO_TICKS(50)); // ~20fps rate limit
  }

  return res;
}

// toggle flash led on/off
static esp_err_t flash_handler(httpd_req_t *req) {
  flashState = !flashState;
  digitalWrite(FLASH_LED_PIN, flashState);
  
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  
  char response[64];
  snprintf(response, sizeof(response), "{\"flash\":%s}", flashState ? "true" : "false");
  
  return httpd_resp_send(req, response, HTTPD_RESP_USE_STRLEN);
}

// return camera settings as json
static esp_err_t status_handler(httpd_req_t *req) {
  sensor_t * s = esp_camera_sensor_get();
  
  char json_response[512];
  snprintf(json_response, sizeof(json_response),
    "{"
    "\"flash\":%s,"
    "\"framesize\":%u,"
    "\"quality\":%u,"
    "\"brightness\":%d,"
    "\"contrast\":%d,"
    "\"saturation\":%d"
    "}",
    flashState ? "true" : "false",
    s->status.framesize,
    s->status.quality,
    s->status.brightness,
    s->status.contrast,
    s->status.saturation
  );

  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  return httpd_resp_send(req, json_response, HTTPD_RESP_USE_STRLEN);
}