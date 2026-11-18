# =============================================================================
# certs/README.md
# =============================================================================

# Directorio de Certificados TLS

Este directorio almacena los certificados TLS para la comunicación segura
entre el backend Python y el broker MQTT.

## ¿Qué va aquí?

| Archivo        | Descripción                                      | Gitignore |
|----------------|--------------------------------------------------|-----------|
| `ca.crt`       | Certificado raíz de la CA (distribúyelo)         | No        |
| `ca.key`       | Clave privada de la CA (**mantén en secreto**)   | **Sí**    |
| `server.crt`   | Certificado del servidor Mosquitto               | No        |
| `server.key`   | Clave privada del servidor (**mantén en secreto**) | **Sí**  |
| `client.crt`   | Certificado del cliente Python                   | No        |
| `client.key`   | Clave privada del cliente (**mantén en secreto**) | **Sí**   |

## Generar certificados de desarrollo

```bash
cd certs/
bash generate_certs.sh
```

Requiere OpenSSL ≥ 1.1.1 instalado.

## ⚠️ Advertencia de seguridad

- **Nunca** subas archivos `*.key` a Git.
- Los certificados auto-firmados son **solo para desarrollo**.
- En producción usa una CA confiable o Let's Encrypt.
