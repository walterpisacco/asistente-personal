# Agents.md — contexto para el coding agent

## Qué es este repo
Bot hecho en Python. Por defecto usa **OpenAI Realtime** (`CONVERSATION_MODE=realtime`, modelo `gpt-realtime-2.1`) speech-to-speech, con transcripción de entrada (`gpt-4o-mini-transcribe`). Alternativa: `pipeline` (STT→LLM→TTS).
El Bot está continuamente escuchando. Tras login, la conversación se activa con wake local («hola»). En modo realtime el audio va por WebSocket a OpenAI; en pipeline el STT corre recién después de activar.
La conversación se desactiva luego de un silencio prolongado (`BOT_END_CALL_MS`) o con la acción finalizar.

## Prompt del bot (runtime)
- Archivo canónico: `backend/prompts/bot_system_prompt.txt`
- Variables de entorno:: `backend/.env`
- Carga: `BOT_SYSTEM_PROMPT_FILE` (o `BOT_SYSTEM_PROMPT` inline, que pisa el archivo)
- Se resuelve en `backend/app/core/config.py` → `resolved_bot_system_prompt` y se pasa al LLM en `backend/app/providers/llm/factory.py`
- Se carga una vez al iniciar el backend; cambios al archivo requieren reiniciar el proceso

### Al editar el prompt
- Mantener español rioplatense, salida solo texto para TTS 
- Preferir frases cortas por turno; 

## Identificación de usuario
El bot autentica con **usuario/contraseña** (mismas credenciales que mobile/web: `AuthService.login` / `POST /api/v1/auth/login`).
La pantalla de login corre al arrancar (`app/bot/login_gate.py`); la sesión queda en `backend/data/bot_session.json`.

La wake word («hola», «hola tori», …) **solo activa** la conversación; ya no identifica a la persona.
El usuario de la sesión se relee de la DB en cada activación.

El bot no debe crear usuarios.

## Inyección de contexto de usuario
El backend inyecta el JSON (CRM) al inicio de la conversación:
Claves relevantes: `id`, `full_name`, `gender`, `age`.

No hardcodear datos de los usuarios en el system prompt ni en este archivo.

## Convenciones de producto
- Flujo: Login (usuario/contraseña) → «hola» → Conversación → Finalización
- Temas: hablar de cualquier tema.
- El bot puede buscar y reproducir videos de YouTube por artista, género o tema (búsqueda general, sin canal de usuario).
- `Agents.md` es contexto para desarrollo en Cursor; no lo lee el LLM
