#pragma once

#ifdef CTW_PROTOTYPE

// GTA: Chinatown Wars city prototype. The map files come from tools/ctw2re3, which converts the player's own copy
// of the game; this build loads them instead of Liberty City and starts the player there without main.scm.

#define CTW_LEVEL_DAT "DATA\\CTW.DAT"

class CCtwPrototype
{
public:
	// Where CTW-Native puts the player when the game starts (port/game/player.h)
	static const float StartX, StartY, StartHeading;

	// Replaces the opening of main.scm: creates the player on the street, sets the camera behind them and fades in.
	static void StartGame(void);
};

#endif
