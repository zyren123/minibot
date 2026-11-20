<div align="center">

# MiniBot

### Un agente IA local, fácil de aprender, escalable y construido con Python puro

<div align="left">

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![PyPI](https://img.shields.io/pypi/v/minibotclaw?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/minibotclaw/)
[![CI](https://img.shields.io/github/actions/workflow/status/zyren123/minibot/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/zyren123/minibot/actions/workflows/ci.yml)
[![Licencia](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Arquitectura](https://img.shields.io/badge/Architecture-Event--Driven-blueviolet?style=flat-square)](https://github.com/zyren123/minibot)

> **Primero hazlo funcionar, luego entiende cómo funciona.** MiniBot ofrece REPL, WebUI, SDK de Python, MCP, Skills, Hooks, Memory y capacidades de colaboración en equipo, ideales para desarrolladores que desean crear rápidamente un agente local con código transparente y fácil de modificar.

**[ Puedes usarlo ]** &nbsp; `REPL local` • `WebUI` • `Python SDK` • `MCP/Skills` • `Colaboración entre múltiples agentes`

[中文](README.md) | [Inglés](docs/README.en.md)

</div>
</div>

---

<div style="display: flex; justify-content: center; align-items: flex-start; gap: 10px;">
  <img style="height: 400px; width: auto;" alt="Claude Code Minibot" src="https://github.com/user-attachments/assets/c4c1cc8e-9c9a-44e0-ab15-2981fa921cea" />
  <img style="height: 400px; width: auto;" alt="Paraglider Minibot" src="https://github.com/user-attachments/assets/7e221968-293b-4324-9a52-9e6ce26c4be9" />
</div>
<p align="center" style="margin-top: 15px; font-size: 1.2em; font-weight: bold; color: #555;">
  Desde la línea de comandos hasta la WebUI y la integración con SDK, MiniBot ofrece un camino continuo para el desarrollo de agentes locales.
</p>

---

## ⚡ ¿Qué puede hacer MiniBot por ti?

- Inicia un agente local interactivo con `minibot`.
- Inicia un servicio WebUI y API con `minibot-web` para probar rápidamente conversaciones multituples, sesiones y conexiones con plataformas.
- Usa `from minibot import Minibot` directamente en Python para integrar un agente en tus propios scripts o aplicaciones.
- Expande las capacidades con herramientas MCP, Skills, Hooks, Memory y Team en lugar de estar limitado por un gran framework.

## 🎯 ¿Por qué no es otro demo de agentes?

- **Primero hay una entrada completa, luego se explica la teoría**: La terminal, WebUI y SDK funcionan directamente, no solo se presenta un código simulado.
- **Código transparente**: No depende de LangChain, AutoGen o LangGraph, lo que facilita el aprendizaje y la modificación.
- **Prioridad local**: El directorio de trabajo, Skills, Memory, Hooks y la configuración de MCP se almacenan en el sistema de archivos, lo que facilita su seguimiento y control.

## 🚀 30 segundos para empezar

```bash
uv tool install minibotclaw

# Tras el primer inicio, sigue las indicaciones para completar la configuración del modelo
# REPL en terminal
minibot

# WebUI
minibot-web
```

- WebUI: En la página `Config`, agrega un proveedor, importa modelos y asigna un modelo de chat al Bot.
- CLI: Tras el inicio, usa `/model config` para configurar de forma interactiva el archivo `.env` global.
- Manual: Edita directamente el archivo `.env` global, cuya ruta predeterminada es `~/.minibot/.env`.

Los campos mínimos necesarios para la configuración son:

```bash
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-xxxx
MODEL_ID=gpt-4o-mini
```

## 👀 ¿Para quién es adecuado / no es adecuado?

Es adecuado para ti si deseas:

- Entender claramente cómo se implementan mecanismos como Agent, Tool Calling, MCP y Skills.
- Probar rápidamente un agente local que sea controlable, modificable y integrable en un proyecto Python.
- Conectar REPL, WebUI, SDK e integración con plataformas con la menor cantidad de capas de abstracción posible.

No es adecuado para ti si prefieres:

- Una solución lista para usar con un sandbox seguro de nivel de producción y un modelo de permisos estricto.
- Una plataforma de agentes altamente productizada y orientada a usuarios no técnicos.
- Un ecosistema abstracto grande y completo en lugar de una implementación básica y legible.

---

## 🧐 ¿Por qué se desarrolló MiniBot?

En el desarrollo actual de IA, estamos rodeados de diversos frameworks (LangChain, AutoGen, etc.), lo que hace que muchos desarrolladores o personas interesadas no comprendan claramente **cómo funcionan realmente los agentes**.

El objetivo de MiniBot es **"Desmitificar los agentes"**. Al leer el código de este proyecto, comprenderás:

1. **La esencia del ciclo ReAct**: Cómo construir una cadena de pensamiento y acción con un bucle `while` nativo de Python y la API de OpenAI.
2. **La lógica subyacente de Tool Calling**: Cómo utilizar el módulo Python `inspect` para convertir automáticamente funciones en JSON Schema.
3. **MCP (Model Context Protocol)**: Cómo implementar el protocolo del lado del cliente sin depender del SDK oficial.
4. **Gestión del contexto de Skills**: Cómo cargar dinámicamente prompts y bases de conocimiento desde el sistema de archivos.

---

## ⚡ Arquitectura y código principales

MiniBot utiliza un diseño modular extremadamente simplificado, sin ninguna cadena compleja de herencia de clases.

### 1. 🔍 Agente principal (ReAct sin framework)
Descarta las complejas abstracciones de Chain/Graph y vuelve a la esencia.
- **Implementación**: `src/minibot/agent.py`
- **Lógica**: Mantiene una cola de mensajes pura `List[Message]` y procesa recursivamente o mediante bucles las respuestas `tool_calls` del LLM.

### 2. 🛠️ Sistema de herramientas nativo (cadena de herramientas nativa)
En lugar de utilizar Pydantic para generar esquemas, se analizan directamente las firmas de las funciones Python.
- **Implementación**: `src/minibot/tools/`
- **Características**: Soporta ejecución de Bash, operaciones de E/S de archivos y un mecanismo de registro dinámico. Soporta **meta-herramientas**, es decir, "herramientas para crear herramientas".

### 3. 🔗 Integración MCP (Protocolo de contexto del modelo)
Compatible con el protocolo MCP de Claude, conecta con todo.
- **Implementación**: `src/minibot/mcp/`
- **Puntos destacados**: Implementa las capas de transporte basadas en `stdio` y `sse`, y adapta automáticamente los recursos MCP como herramientas que el agente puede invocar.

### 4. 🎣 Hooks y ciclo de vida (ganchos del ciclo de vida)
Basado en un simple patrón de observador, es una capa de seguridad y supervisión.
- **Implementación**: `src/minibot/hooks/`
- **Uso**: Intercepta comandos potencialmente peligrosos en `pre_tool_call` y registra auditorías en `post_agent_loop`.

### 5. 📚 Cargador de Skills (Habilidades dinámicas)
- **Implementación**: `src/minibot/skills/`
- **Lógica**: Similar a los Proyectos de Claude, lee automáticamente archivos Markdown e inyecta prompts en el sistema.

---

## 🚀 Primeros pasos

Utilizamos `uv` para una gestión moderna de paquetes Python (aunque también es compatible con pip).

### Instalación

```bash
uv tool install minibotclaw

# Ejecutar REPL
minibot

# Iniciar WebUI
minibot-web
```

Actualización:

```bash
uv tool upgrade minibotclaw
```

Instalación desde el código fuente (para desarrollo):

```bash
git clone https://github.com/zyren123/minibot.git
cd minibot

# Instalación rápida de dependencias
uv sync

# Los recursos estáticos de WebUI no se envían al repositorio, es necesario construirlos primero para ejecutar el código fuente
cd webui
npm install
npm run build
cd ..
```

### Configuración

Al iniciar por primera vez, MiniBot generará automáticamente un archivo `.env` en el directorio global de la aplicación, cuya ubicación predeterminada es `~/.minibot/.env`.

Puedes completar la configuración a través de la página `Config` de WebUI o del CLI `/model config`; si prefieres editar manualmente, los campos mínimos necesarios son:

```bash
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=sk-xxxx
MODEL_ID=gpt-4o-mini
# Opcional: Activar la estética terminal rica de Rich, similar a Claude Code
MINIBOT_RICH=1
```

### Iniciar REPL

```bash
# Instalación desde PyPI / uv tool
minibot

# Instalación desde código fuente (uv sync)
uv run minibot
```

Comandos útiles:

- `/help`
- `/info`
- `/stream [on|off|status]`

### Iniciar WebUI (en el equipo local)

```bash
# Instalación desde PyPI / uv tool
minibot-web

# Instalación desde código fuente (requiere una ejecución previa de webui/npm run build)
uv run minibot-web
```

Luego abre: `http://127.0.0.1:7860/`

Modo de desarrollo (separación de frontend y backend):

```bash
# Terminal A
uv run minibot-web --reload

# Terminal B
cd webui
npm install
npm run dev
```

### Integración con la plataforma Feishu (WebSocket)

1. Crea una aplicación integrada para empresas en la plataforma Feishu y habilita la capacidad de robot.
2. Suscríbete a los eventos `im.message.receive_v1` para la aplicación y otorga los permisos necesarios para enviar mensajes.
3. Inicia `minibot-web` y abre la pestaña `Platforms` de WebUI.
4. Crea una nueva conexión de plataforma `Feishu`, completa `App ID` y `App Secret` y vincula al Bot objetivo.
5. Si eliminas dicho Bot, la conexión de la plataforma se volverá a vincular automáticamente al `Minibot` predeterminado y continuará procesando mensajes desde una nueva sesión.

Limitaciones actuales:
- Solo se admiten mensajes de texto en chats privados de Feishu.
- Utiliza el modo de conexión WebSocket de larga duración, sin depender de webhook públicos.
- La respuesta se devuelve como un mensaje de texto final, sin envío de tokens de flujo o tarjetas de mensajes.

### Uso del SDK (Python)

No fluido:

```python
from minibot import Minibot

agent = Minibot(system_prompt="Eres un asistente en chino.")
result = agent.chat_sync("Hola")
print(result.assistant_text)
```

Flujo (eventos) asíncrono:

```python
import asyncio
from minibot import Minibot

async def main():
    agent = Minibot()
    async for ev in agent.stream("Cuéntame un chiste"):
        if ev.get("type") == "assistant_delta":
            print(ev.get("delta_text", ""), end="", flush=True)

asyncio.run(main())
```

Registrar herramientas personalizadas (pasar funciones Python directamente):

```python
from minibot import Minibot

def echo(text: str) -> str:
    return text

agent = Minibot(tools=[echo])
```

---

## 💻 Guía de lectura del código fuente (Dónde aprender)

Esta es una guía de aprendizaje que te indica qué concepto muestra cada parte del código:

```text
src/minibot/
├── agent.py             # [Central] Aquí se entiende cómo se implementa manualmente el ciclo de pensamiento y acción del LLM
├── core/
│   └── client.py        # Encapsulación del SDK de OpenAI, maneja la salida fluida y el modo multimodal
├── tools/
│   ├── base.py          # [Importante] Cómo utilizar la biblioteca inspect para convertir funciones Python en JSON Schema
│   └── registry.py      # Simple tabla de búsqueda que implementa la distribución de herramientas
├── mcp/
│   ├── client.py        # [Avanzado] Implementación del cliente de protocolo MCP con manos propias, comprender JSON-RPC 2.0
│   └── transport.py     # Implementación de la comunicación entre procesos (Stdio/SSE)
├── skills/
│   └── loader.py        # Cómo analizar el sistema de archivos y construir dinámicamente el contexto del prompt
└── hooks/
    └── executor.py      # Implementación del patrón de middleware para la interceptación segura
```

---

## 🎮 Ejemplos de interacción

MiniBot ofrece una interfaz de terminal moderna basada en `prompt_toolkit` y `Rich`. Sí, el readme está escrito con minibot.

<img width="1310" height="1176" alt="image" src="https://github.com/user-attachments/assets/cbe0ad45-8ec9-40c5-b32c-8c8a0b9634ef" />


---

## 🔧 Desarrollo de extensiones

### 1. Escribir una herramienta puramente Python

No es necesario heredar clases complejas, solo hay que definir una función y sus anotaciones de tipo:

```python
from minibot.tools.base import BaseTool

class WeatherTool(BaseTool):
    name = "get_weather"
    description = "Obtener el clima de una ciudad"

    # Las anotaciones de tipo se convierten automáticamente en Tool Schema
    async def execute(self, city: str, unit: str = "celsius") -> str:
        # Aquí escribe la lógica nativa de Python
        return f"El clima en {city} es de 25 grados ({unit})"
```

### 2. Acceder a un servidor MCP

Configura el servidor MCP en `config/mcp_servers.yaml` sin necesidad de modificar el código para expandir las capacidades (por ejemplo, conectar con GitHub, Postgres, etc.):

```yaml
servers:
  - name: github-mcp
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: "your-token"
```

---

## 👥 Equipos de agentes (MVP)

MiniBot ahora soporta **equipos de agentes en proceso** (organización de equipos en una sesión):

- El líder puede decidir de forma autónoma si crear un equipo y cuántos miembros (predeterminado 3, máximo 6)
- Los compañeros tienen capacidades completas (lectura/escritura de archivos, bash, MCP, memoria, etc.), pero **no pueden crear miembros adicionales** (desactivado `Task`/`TeamCreate`/`TeamShutdown`)
- Cualquier miembro puede comunicarse punto a punto (`TeamMessage`) o emitir una transmisión a todo el equipo (`TeamBroadcast`)
- Se proporciona un ligero tablero de tareas compartido (`TeamTask`: create/list/assign/claim/complete)
- Se proporciona `TeamWait` para que el líder espere y resuma los eventos de los compañeros

### Herramientas de equipo disponibles

- `TeamCreate`
- `TeamMembers`
- `TeamTask`
- `TeamMessage`
- `TeamBroadcast`
- `TeamWait` (solo líder)
- `TeamShutdown` (solo líder)

### Limitaciones actuales

- Solo se admiten equipos **dentro de una sola sesión**, sin posibilidad de recuperación entre reinicios
- No se admite el modo de pantalla dividida tmux/iTerm2 (MVP solo en proceso)
- No se admiten equipos anidados (los compañeros no pueden crear agentes secundarios)

### Configuración relacionada

`config/default.yaml`:

```yaml
llm:
  stream_enabled: true

teams:
  quiet_teammates: true
  debug_teammate_output: false
```

Al estar activado, los compañeros no emitirán al terminal filas de estado de Thinking/Running ni contenido normal, evitando que la salida concurrente contamine el terminal principal.
El agente principal (solo/ líder) habilita de forma predeterminada la salida fluida del texto principal; si la pasarela no soporta flujo, se volverá automáticamente a la salida no fluida.

---

## 🗺️ Hoja de ruta

- [x] **Soporte de memoria a largo plazo**: Memoria de contexto persistente basada en el sistema de archivos local
- [x] **Equipos de agentes (MVP)**: Equipos concurrentes en una sesión, bus de mensajes, tablero de tareas, protección contra bloqueos de concurrencia
- [ ] **Visión**: Soporte nativo para la comprensión de imágenes multimodales
- [ ] **Sandbox**: Sandbox de ejecución de herramientas basado en Docker
- [ ] **Interfaz web**: API ligera basada en FastAPI

---

## 🤝 Contribuciones y licencia

Este proyecto se rige bajo la licencia [MIT](LICENSE).

¡Bienvenido a enviar PRs! Si deseas aprender sobre los principios de los agentes, la mejor manera es intentar modificar la lógica del bucle principal en `src/minibot/agent.py`.

---

<div align="center">
Hecho con ❤️ por ingenieros, para ingenieros.
</div>
