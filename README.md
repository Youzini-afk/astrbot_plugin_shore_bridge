# astrbot_plugin_shore_bridge

AstrBot plugin that connects message turns to the `shore-memory` server.

## Behaviors

- injects long-term memory context before the model request
- writes completed user/assistant turns back after the model response

## Install

Clone this directory into `AstrBot/data/plugins/astrbot_plugin_shore_bridge`, then install the `requirements.txt` dependencies and configure the plugin in AstrBot WebUI.
