# JoyconVR

Use **Joy-Cons** (Switch 1 e **Joy-Con 2** do Switch 2) como controles de VR no **SteamVR**, sem óculos: o jogo aparece numa janela no monitor.

## Baixar
Vá em **[Releases](../../releases/latest)** e baixe o `JoyconVR.zip`. Extraia numa pasta fixa (ex.: `C:\JoyconVR`) e leia o `LEIA-ME.txt`.

- **Joy-Con 1 (Switch 1):** pareie no Bluetooth do Windows, rode `INSTALAR.bat` e abra o `JoyconVR-Joycon2.exe` (ele mostra e ajusta o Joy-Con 1 também). Sem a janela aberta, o SteamVR lê o Joy-Con 1 direto, como antes.
- **Joy-Con 2 (Switch 2):** rode `INSTALAR.bat` e abra o `JoyconVR-Joycon2.exe` (não precisa de Python).

O `JoyconVR-Joycon2.exe` avisa sozinho quando há versão nova e se atualiza com um clique, mantendo seus ajustes.

## O que tem
- Headset virtual numa janela (detecta o tamanho do monitor sozinho).
- Controles como Valve Index (funciona em VRChat, Roblox VR e outros jogos SteamVR/OpenXR).
- Giroscópio + modelo de braço para as mãos; analógico direito gira a câmera.
- Janela com botões ao vivo, troca de botões, sensibilidade, zona morta, trocar mãos.
- Ajustes do `config.json` valem na hora, sem reiniciar o SteamVR.
- **Câmera pela cabeça (webcam):** vire um pouco a cabeça e a visão gira mais. Tudo roda no PC, nada é salvo ou enviado; a janela mostra só os pontos do rosto. Usa o [MediaPipe Face Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker) do Google (Apache 2.0).
- Luzes, luz do HOME, vibração e bateria na janela.

## Compilar
- Driver: `codigo-fonte/build_zig.sh <deps> <python com ziglang>` (hidapi 0.14.0 e nlohmann/json 3.11.3 em `deps/`).
- Janela: PyInstaller em `switch2/joyconvr_gui.py` (requer `bleak`).

Baseado em [JoyconInputOpenVR](https://github.com/LogicCuteGuy/JoyconInputOpenVR) (MIT). Protocolo do Joy-Con 2 conferido com o driver do [SDL](https://github.com/libsdl-org/SDL). Licença MIT.
