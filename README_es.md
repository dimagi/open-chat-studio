# Open Chat Studio

<!-- hy-mt2-i18n:start -->
[English](./README.md) | [中文](./README_zh-CN.md) | [日本語](./README_ja.md) | **Español**
<!-- hy-mt2-i18n:end -->

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/dimagi/open-chat-studio) [![codecov](https://codecov.io/github/dimagi/open-chat-studio/graph/badge.svg?token=SUKZMAWM3O)](https://codecov.io/github/dimagi/open-chat-studio)

Open Chat Studio es una plataforma para desarrollar, desplegar y evaluar aplicaciones de chat impulsadas por inteligencia artificial. Ofrece herramientas para trabajar con diversos modelos de lenguaje grande (LLM), crear chatbots, gestionar conversaciones e integrarse con diferentes plataformas de mensajería.

[Documentación para usuarios](https://docs.openchatstudio.com) | [Documentación para desarrolladores](https://developers.openchatstudio.com/)

## Contribuciones

¡Damos la bienvenida a las contribuciones para Open Chat Studio! Si está interesado en colaborar, consulte nuestras [pautas de contribución](https://developers.openchatstudio.com/contributing/) para obtener más información sobre cómo comenzar.

## Tecnologías utilizadas
- **Backend:** Python 3.13+, Django, Django REST Framework, Celery
- **Base de datos:** PostgreSQL (con pgvector)
- **Almacenamiento en caché/Corredor de mensajes:** Redis
- **Frontend:** TypeScript, CSS ([Tailwind](http://tailwindcss.com/) + [DaisyUI](https://daisyui.com/)), HTMX, Alpine.js, webpack, [ReactJS](https://react.dev/) con [React Flow](https://reactflow.dev/) (para componentes específicos)
- **Integraciones con LLM:** OpenAI, Anthropic, Groq, Gemini, Azure y más
- **Despliegue:** Docker, Heroku

## Configuración para comenzar rápidamente

Open Chat Studio utiliza [UV](https://docs.astral.sh/uv/getting-started/installation/) y [Invoke](https://www.pyinvoke.org/) para la automatización del desarrollo.

### Requisitos previos

- Python 3.13 (recomendado)
- Node.js >= 24.0.0
- Docker y Docker Compose

### Configuración

```bash
git clone https://github.com/dimagi/open-chat-studio.git
cd open-chat-studio
uv venv --python 3.13
source.venv/bin/activate
uv sync
inv setup-dev-env   # instala ganchos, inicia los servicios, migra la base de datos, construye el frontend y crea un superusuario
./manage.py runserver
```

Ejecuta Celery en una terminal separada: es necesario para las interacciones con los modelos de lenguaje grande.

```bash
inv celery
```

Para obtener instrucciones completas de configuración, incluidos los pasos manuales, la configuración del entorno y la resolución de problemas, consulte la [guía de configuración para desarrollo local](https://developers.openchatstudio.com/getting-started/local-setup/).

## Entorno de desarrollo exclusivo con Docker

Como alternativa a ejecutar Django y Celery en el equipo host, puede ejecutar toda la pila dentro de Docker: no es necesario instalar Python ni Node localmente.

```bash
cp.env.example.env   # establezca al menos SECRET_KEY
docker compose build
docker compose up
```

Para consultar la guía completa de configuración, los servicios disponibles, los comandos útiles y las soluciones de problemas, visite la [Guía de configuración del entorno de desarrollo con Docker](https://developers.openchatstudio.com/getting-started/docker-setup/).

## Despliegue

Para desplegar tu propia instancia de producción en Heroku:

[![Desplegar](https://www.herokucdn.com/deploy/button.svg)](https://www.heroku.com/deploy?template=https://github.com/dimagi/open-chat-studio)

## Obtener ayuda

- **Informes de errores y solicitudes de funcionalidades:** [Cómo enviar comentarios](https://developers.openchatstudio.com/contributing/)
- **Documentación para desarrolladores:** [developers.openchatstudio.com](https://developers.openchatstudio.com/)
- **Documentación para usuarios:** [docs.openchatstudio.com](https://docs.openchatstudio.com)
