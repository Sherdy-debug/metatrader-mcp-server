# Plan del fork y revisión de MetaTrader MCP Server

Fork de trabajo: https://github.com/Xainner/metatrader-mcp-server

Este fork se mantiene para corregir los problemas detectados en la revisión antes de habilitar operaciones automáticas. Esta entrega añade documentación; los arreglos del código quedan pendientes para una próxima sesión.

## Arreglos pendientes

- [ ] Interpretar `retcode`, manejar resultados nulos y distinguir aceptación, ejecución parcial, ejecución completa y rechazo.
- [ ] Preservar SL/TP al modificar únicamente el precio de una orden pendiente.
- [ ] Exponer SL/TP en las órdenes de mercado del cliente y MCP; obtener el precio antes de validar sus niveles.
- [ ] Usar un transporte local por defecto y proteger cualquier acceso remoto con autenticación.
- [ ] Comprobar y comunicar cada cierre individual en las operaciones masivas.
- [ ] Corregir la selección del modo de llenado según símbolo, tipo de ejecución y clase de orden.
- [ ] Validar volumen mínimo, máximo y paso, precisión del precio y distancia mínima de los stops según el símbolo.
- [ ] Incorporar límites configurables de riesgo, exposición y pérdida diaria, y prevención de solicitudes duplicadas.
- [ ] Aislar las pruebas automáticas con simulaciones; exigir cuenta demo explícita para pruebas de integración que operen.
- [ ] Verificar arranque, esquemas MCP y compatibilidad de dependencias; fijar versiones probadas.
- [ ] Validar los cambios en una cuenta demo dedicada antes de considerar uso real.

Las casillas solo deben marcarse tras implementar y verificar cada cambio. El alcance de esta revisión y las limitaciones de sus pruebas se describen a continuación.

---

## Revisión inicial

Fecha: 20 de octubre de 2026.
Repositorio: https://github.com/ariadng/metatrader-mcp-server
Commit revisado: `bd2ff591e32cb492fafefe74221d502dd78b71ef`.
Versión declarada: 0.5.1.

## Dictamen

El proyecto implementa un puente real entre MCP y la API oficial de MetaTrader 5, pero esta revisión encuentra defectos que impiden recomendarlo tal como está para operar dinero real de forma autónoma. Puede servir como base después de corregir la ejecución, proteger el acceso y verificarlo en demo.

## Hallazgos

1. **Alta: operaciones rechazadas se anuncian como exitosas.** En `src/metatrader_client/order/send_order.py:197–204` y ramas equivalentes, se consulta `last_error()` sin interpretar `response.retcode`. Una respuesta simulada del bróker con `retcode=10019` (fondos insuficientes) produjo `error=false` y un mensaje de compra exitosa con ticket 0. Se debe distinguir rechazo, aceptación, ejecución completa y ejecución parcial, conservar los códigos y reconciliar el estado con el terminal. Referencia oficial: https://www.mql5.com/en/docs/python_metatrader5/mt5ordersend_py

2. **Alta: modificar una orden pendiente puede eliminar sus protecciones.** `modify_pending_order.py` omite los parámetros no proporcionados, pero `send_order.py:295–296` los reemplaza por sus valores predeterminados 0.0. La simulación de cambiar únicamente el precio envió también `sl=0.0` y `tp=0.0`. Si el servidor acepta la solicitud, esos valores solicitan retirar las protecciones. Deben preservarse los valores actuales salvo eliminación explícita.

3. **Alta: apertura de mercado sin SL/TP por MCP.** `src/metatrader_mcp/server.py:149` y `src/metatrader_client/client_order.py:60` solo aceptan símbolo, volumen y dirección. La función interna sí tiene parámetros SL/TP, pero no se exponen por esas capas. Además, al llamar directamente a esa función, un BUY a mercado con SL válido se rechaza porque `send_order.py:140` compara el SL con el precio predeterminado 0 antes de obtener la cotización. Se reprodujo con SL 1.09 y ask simulado 1.1001; no llegó a enviar la solicitud.

4. **Alta: servidor MCP accesible por red sin autenticación configurada.** `src/metatrader_mcp/utils.py:8–24` elige SSE y `0.0.0.0` por defecto y desactiva la protección frente a DNS rebinding para esa dirección. La creación de FastMCP no configura autenticación. Un cliente con acceso de red al servicio podría acceder a herramientas de trading; la accesibilidad efectiva depende del firewall y la red. Para uso local conviene STDIO. Cualquier exposición remota requiere autenticación y transporte protegido.

5. **Alta: cierre masivo con éxito falso.** `src/metatrader_client/order/close_all_positions.py` ignora el resultado de cada cierre y aumenta el contador igualmente. Al simular dos cierres fallidos, devolvió `Close 2 positions success`. Debe informar cada resultado y comprobar las posiciones restantes.

6. **Media: selección incorrecta del modo de llenado.** `send_order.py:106–115` asocia el bit 4 con RETURN, aunque ese bit corresponde a BOC, y no establece alternativa si la máscara vale 0. Una simulación con máscara 0 produjo `UnboundLocalError` antes de enviar la orden. La elección también debe considerar el tipo de ejecución y si se trata de una orden pendiente. Referencia: https://www.mql5.com/en/docs/constants/environment_state/marketinfoconstants#symbol_filling_mode

7. **Controles insuficientes para autonomía.** El envío solo aplica un límite genérico de volumen de hasta 100 lotes. No encontré controles efectivos de pérdida diaria, riesgo por operación, exposición acumulada, repetición de solicitudes ni un bloqueo de cuentas reales en el recorrido de ejecución revisado. Estos límites deben imponerse por código fuera del criterio del agente.

## Validación realizada

- Lectura del código de conexión, herramientas MCP, envío y gestión de órdenes, interfaces HTTP y pruebas.
- Ejecución aislada de funciones extraídas del código original, con API MT5 y datos simulados. Se verificaron los seis casos descritos como reproducidos o comprobados por firma; no se importó la API real ni se inició sesión.
- Resolución de dependencias con `uv pip install --dry-run` en un entorno virtual Python 3.12: completada. No demuestra que el servidor arranque; las dependencias tienen mínimos sin límites superiores y hoy resuelven, entre otras, MCP 2.3.0 y pandas 3.0.6.
- Confirmado el proceso `terminal64.exe` y su ejecutable en `C:\Program Files\MetaTrader 5\terminal64.exe`.
- Disponibles Python 3.10, 3.11, 3.12 y 3.14; se utilizó 3.12 para comprobar resolución.

No se instaló ni arrancó el servidor, no se leyó la cuenta y no se enviaron órdenes. No se ejecutó la suite original porque contiene pruebas que abren operaciones y cierran posiciones usando credenciales del entorno, sin imponer que la cuenta sea demo. Esta revisión no certifica las dependencias ni el paquete publicado en PyPI: examina el commit indicado.

## Trabajo necesario antes de operar

Corregir los resultados y las modificaciones de órdenes, permitir SL/TP en la apertura, elegir correctamente el modo de llenado y comprobar el volumen permitido por cada símbolo. Después, configurar acceso local, limitar riesgo y exposición, evitar duplicados y probar apertura, modificación, rechazo, llenado parcial y cierre en una cuenta demo dedicada. Tener MT5 instalado cumple un requisito, pero no valida la ejecución del MCP.
