#include "common.h"

#ifdef CTW_PROTOTYPE

#include "ctw.h"
#include "Camera.h"
#include "CarCtrl.h"
#include "Clock.h"
#include "ModelIndices.h"
#include "PathFind.h"
#include "PlayerPed.h"
#include "Population.h"
#include "Restart.h"
#include "Streaming.h"
#include "TxdStore.h"
#include "FileMgr.h"
#include "World.h"
#include "Zones.h"

const float CCtwPrototype::StartX = -1000.0f;
const float CCtwPrototype::StartY = -1590.0f;
const float CCtwPrototype::StartHeading = 0.0f;
static const int16 PedDensity = 12;

void
CCtwPrototype::StartGame(void)
{
	CVector pos(StartX, StartY, 0.0f);

	// Without main.scm no zone sets a pedestrian density, so give the whole city one
	if(ThePaths.m_numPedPathNodes > 0){
		CTheZones::SetPedDensity(0, 1, PedDensity);	// zone 0 covers the whole map, by day
		CTheZones::SetPedDensity(0, 0, PedDensity);	// and by night
	}
	if(ThePaths.m_numCarPathNodes == 0)
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

struct CtwPathLink
{
	uint32 a, b;	// a < b
	uint8 lanesToA, lanesToB, flags, pad;
};

void
CCtwPrototype::LoadPaths(void)
{
	CPathFind &paths = ThePaths;
	paths.m_numPathNodes = paths.m_numCarPathNodes = paths.m_numPedPathNodes = 0;
	paths.m_numConnections = 0;
	paths.m_numCarPathLinks = 0;

	int file = CFileMgr::OpenFile("DATA\\MAPS\\CTW\\CTW_PATHS.BIN", "rb");
	if(file == 0){
		debug("CTW: data/maps/ctw/ctw_paths.bin not found, so no traffic or pedestrians; convert the city again with tools/ctw2re3\n");
		return;
	}
	char magic[8];
	uint32 counts[3];
	CFileMgr::Read(file, magic, 8);
	CFileMgr::Read(file, (char*)counts, sizeof(counts));
	uint32 numCar = counts[0], numPed = counts[1], numLinks = counts[2];
	uint32 numNodes = numCar + numPed;
	if(memcmp(magic, "CTWPATH1", 8) != 0 || numNodes > NUM_PATHNODES || 2*numLinks > NUM_PATHCONNECTIONS){
		debug("CTW: ctw_paths.bin is not one this build can load (%u nodes, %u links)\n", numNodes, numLinks);
		CFileMgr::CloseFile(file);
		return;
	}
	float *pos = new float[numNodes*3];
	CtwPathLink *links = new CtwPathLink[numLinks];
	CFileMgr::Read(file, (char*)pos, numNodes*3*sizeof(float));
	CFileMgr::Read(file, (char*)links, numLinks*sizeof(CtwPathLink));
	CFileMgr::CloseFile(file);

	uint32 i, j;
	for(i = 0; i < numNodes; i++){
		CPathNode &n = paths.m_pathNodes[i];
		n.SetPosition(CVector(pos[i*3], pos[i*3+1], pos[i*3+2]));
		n.SetPrev(nil);
		n.SetNext(nil);
		n.distance = INT16_MAX-1;	// MAX_DIST in PathFind.cpp
		n.objectIndex = 0;
		n.numLinks = 0;
		n.unkBits = 1;	// FindNodeClosestToCoors only looks at these
		n.bDeadEnd = false;
		n.bDisabled = false;
		n.bBetweenLevels = false;
		n.group = 0;
	}

	// every link is a connection from both of its nodes, stored node by node
	int32 *count = new int32[numNodes];
	memset(count, 0, numNodes*sizeof(int32));
	bool ok = true;
	for(i = 0; i < numLinks; i++){
		if(links[i].a >= numNodes || links[i].b >= numNodes || (links[i].a < numCar) != (links[i].b < numCar)){
			ok = false;
			break;
		}
		count[links[i].a]++;
		count[links[i].b]++;
	}
	for(i = 0; ok && i < numNodes; i++)
		if(count[i] > 255)
			ok = false;
	uint32 carLinks = 0;
	for(i = 0; i < numLinks; i++)
		if(links[i].a < numCar)
			carLinks++;
	if(!ok || carLinks > NUM_CARPATHLINKS){
		debug("CTW: ctw_paths.bin has bad links\n");
		delete[] pos;
		delete[] links;
		delete[] count;
		return;
	}
	int32 first = 0;
	for(i = 0; i < numNodes; i++){
		paths.m_pathNodes[i].firstLink = first;
		first += count[i];
	}
	for(i = 0; i < numLinks; i++){
		const CtwPathLink &l = links[i];
		CPathNode &na = paths.m_pathNodes[l.a];
		CPathNode &nb = paths.m_pathNodes[l.b];
		int32 ca = na.firstLink + na.numLinks++;
		int32 cb = nb.firstLink + nb.numLinks++;
		float dist = (na.GetPosition() - nb.GetPosition()).Magnitude();
		paths.m_connections[ca] = l.b;
		paths.m_connections[cb] = l.a;
		paths.m_distances[ca] = paths.m_distances[cb] = Min(dist, 32000.0f);
		paths.m_connectionFlags[ca].flags = paths.m_connectionFlags[cb].flags = 0;
		paths.m_connectionFlags[ca].bCrossesRoad = paths.m_connectionFlags[cb].bCrossesRoad = (l.flags & 1) != 0;
		if(l.a < numCar){
			// As PreparePathDataForType makes them: the link points from the higher node to the lower one, and
			// "left" lanes are the ones driving that way
			CCarPathLink &cl = paths.m_carPathLinks[paths.m_numCarPathLinks];
			CVector2D d = na.GetPosition() - nb.GetPosition();
			float mag = d.Magnitude();
			cl.pos = (CVector2D(na.GetPosition()) + CVector2D(nb.GetPosition())) * 0.5f;
			cl.dir = mag > 0.0f ? d / mag : CVector2D(1.0f, 0.0f);
			cl.pathNodeIndex = l.a;
			cl.numLeftLanes = l.lanesToA;
			cl.numRightLanes = l.lanesToB;
			cl.trafficLightType = 0;
			cl.bBridgeLights = false;
			paths.m_carPathConnections[ca] = paths.m_carPathConnections[cb] = paths.m_numCarPathLinks++;
		}else
			paths.m_carPathConnections[ca] = paths.m_carPathConnections[cb] = 0;
	}
	paths.m_numConnections = first;
	paths.m_numPathNodes = numNodes;
	paths.m_numCarPathNodes = numCar;
	paths.m_numPedPathNodes = numPed;

	// as PreparePathDataForType does: a node is a dead end when fewer than two of its neighbours lead anywhere
	bool cont;
	do{
		cont = false;
		for(i = 0; i < numCar; i++){
			CPathNode &n = paths.m_pathNodes[i];
			if(n.bDeadEnd)
				continue;
			int k = 0;
			for(j = 0; j < n.numLinks; j++)
				if(!paths.m_pathNodes[paths.ConnectedNode(n.firstLink + j)].bDeadEnd)
					k++;
			if(k < 2){
				n.bDeadEnd = true;
				cont = true;
			}
		}
	}while(cont);

	paths.CountFloodFillGroups(PATH_CAR);
	paths.CountFloodFillGroups(PATH_PED);
	debug("CTW: loaded %u road nodes, %u pavement nodes and %u links\n", numCar, numPed, numLinks);
	delete[] pos;
	delete[] links;
	delete[] count;
}

#endif
