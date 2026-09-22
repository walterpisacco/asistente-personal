# Agents.md — contexto para el coding agent

## Qué es este repo
Bot hecho en Python. El LLM conversa con el usuario; STT/TTS.
El Bot está continuamente escuchando y la converación se activa cuando escucha la palabra clave TORI. 
Los cambios de turno se realizan por tiempo de silencios cortos.
La conversación desactiva luego de un silencio prolongado.

## Prompt del bot (runtime)
- Archivo canónico: `backend/prompts/bot_system_prompt.txt`
- Variables de entorno:: `backend/.env`
- Carga: `BOT_SYSTEM_PROMPT_FILE` (o `BOT_SYSTEM_PROMPT` inline, que pisa el archivo)
- Se resuelve en `backend/config.py` → `resolved_bot_system_prompt` y se pasa al LLM en `backend/llm/factory.py`
- Se carga una vez al iniciar el backend; cambios al archivo requieren reiniciar el proceso

### Al editar el prompt
- Mantener español rioplatense, salida solo texto para TTS 
- Preferir frases cortas por tuerno; 
- Plantillas `{{nombre}}` / campos del JSON se resuelven con el contexto de la conversación, no hardcodear personas

## Identificación de usuario
Cuando se activa la conversación con la palabra clave TORI, el backend busca el embeddings de la voz en la bae de datos y devuelve el nombre, genero, edad e ID de usuario.

## Inyección de contexto de usuario
El backend inyecta el JSON (CRM) al inicio de la conversación:
Claves relevantes: `id`, `nombre`, `genero`, `edad`.

No hardcodear datos de los usuarios en el system prompt ni en este archivo.

## Convenciones de producto
- Flujo: Inicio → Identificación → Conversación → Finalización
- Temas: hablar de cualquier tema
- `Agents.md` es contexto para desarrollo en Cursor; no lo lee el LLM
