# Agents.md — contexto para el coding agent

## Qué es este repo
Bot hecho en Python. El LLM conversa con el usuario; STT/TTS.
El Bot está continuamente escuchando. La conversación se activa en local: VAD y keyword spotting de HOLA TORI. El STT en la nube corre recién después de esa activación.
El Bot está contunuamente haciendo reconicimiento de imagen y la conversación se activa cuando encuentra un rostro. 
Los cambios de turno en la conversacion se realizan por tiempo de silencios cortos configurado en BOT_SILENCE_MS.
La conversación se desactiva luego de un silencio prolongado configurado en BOT_END_CALL_MS.

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
Cuando se activa la conversación con la palabra clave HOLA TORI, el backend busca el embeddings de la voz en la base de datos y devuelve el id, full_name, gender, age, youtube_profile.

Cuando se activa la conversación con la identificación de rostro, el backend busca el embeddings del rostro en la base de datos y devuelve el id, full_name, gender, age, youtube_profile.


El bot No debe crear usuarios.

## Inyección de contexto de usuario
El backend inyecta el JSON (CRM) al inicio de la conversación:
Claves relevantes: `id`, `full_name`, `gender`, `age`, `youtube_profile`.

No hardcodear datos de los usuarios en el system prompt ni en este archivo.

## Convenciones de producto
- Flujo: Palabra Clave → Inicio → Identificación → Conversación → Finalización
- Temas: hablar de cualquier tema.
- el Bot puede reproducir canciones aleatorias del perfil de youtube filtrando por artista o genero musical.
- `Agents.md` es contexto para desarrollo en Cursor; no lo lee el LLM
