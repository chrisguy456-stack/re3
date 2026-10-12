#include "common.h"

#ifdef CTW_PROTOTYPE

#include "ctw.h"
#include "Camera.h"
#include "CarCtrl.h"
#include "Clock.h"
#include "ModelIndices.h"
#include "PlayerPed.h"
#include "Population.h"
#include "Restart.h"
#include "Streaming.h"
#include "TxdStore.h"
#include "FileMgr.h"
#include "World.h"

const float CCtwPrototype::StartX = -1000.0f;
const float CCtwPrototype::StartY = -1590.0f;
const float CCtwPrototype::StartHeading = 0.0f;

void
CCtwPrototype::StartGame(void)
{
	CVector pos(StartX, StartY, 0.0f);

	// The city has no car or pedestrian paths yet, so nothing can be generated on them.
	CPopulation::PedDensityMultiplier = 0.0f;
	CCarCtrl::CarDensityMultiplier = 0.0f;

	// As COMMAND_CREATE_PLAYER does it
	debug("CTW: loading the city around %.0f %.0f\n", pos.x, pos.y);
	CStreaming::LoadScene(pos);
	debug("CTW: city loaded\n");
	if(!CStreaming::HasModelLoaded(MI_PLAYER)){
		CStreaming::RequestSpecialModel(MI_PLAYER, "player", STREAMFLAGS_DONT_REMOVE | STREAMFLAGS_DEPENDENCY);
		CStreaming::LoadAllRequestedModels(false);
	}
	CPlayerPed::SetupPlayerPed(0);
	CPlayerPed *player = CWorld::Players[0].m_pPed;
	player->CharCreatedBy = MISSION_CHAR;
	CPlayerPed::DeactivatePlayerPed(0);
	pos.z = CWorld::FindGroundZForCoord(pos.x, pos.y) + player->GetDistanceFromCentreOfMassToBaseOfModel();
	player->SetPosition(pos);
	player->m_fRotationCur = player->m_fRotationDest = DEGTORAD(StartHeading);
	player->SetHeading(DEGTORAD(StartHeading));
	CPlayerPed::ReactivatePlayerPed(0);
	debug("CTW: player placed at %.1f %.1f %.1f\n", pos.x, pos.y, pos.z);

	CRestart::AddHospitalRestartPoint(pos, StartHeading);
	CRestart::AddPoliceRestartPoint(pos, StartHeading);
	CClock::SetGameClock(12, 0);

	TheCamera.SetCameraDirectlyBehindForFollowPed_CamOnAString();
	TheCamera.RestoreWithJumpCut();
	TheCamera.Fade(1.0f, FADE_IN);
	debug("CTW: game started\n");
}

static rw::TexDictionary *sharedTxd;
static rw::Texture *(*prevFindCB)(const char *name);

static rw::Texture*
FindWithSharedFallback(const char *name)
{
	rw::Texture *tex = prevFindCB(name);
	if(tex == nil && sharedTxd)
		tex = sharedTxd->find(name);
	return tex;
}

void
CCtwPrototype::LoadSharedTextures(void)
{
	// CTxdStore::LoadTxd waits for a file that doesn't exist, so check first
	int file = CFileMgr::OpenFile("MODELS\\CTW_SHARED.TXD", "rb");
	if(file == 0){
		debug("CTW: models/ctw_shared.txd not found; convert the city again with tools/ctw2re3\n");
		return;
	}
	CFileMgr::CloseFile(file);
	int slot = CTxdStore::AddTxdSlot("ctw_shared");
	CTxdStore::LoadTxd(slot, "MODELS\\CTW_SHARED.TXD");
	CTxdStore::AddRef(slot);
	sharedTxd = CTxdStore::GetSlot(slot)->texDict;
	prevFindCB = rw::Texture::findCB;
	rw::Texture::findCB = FindWithSharedFallback;
	debug("CTW: loaded the shared city textures\n");
}

#endif
