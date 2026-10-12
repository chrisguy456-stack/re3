// SPDX-License-Identifier: GPL-3.0-only
// Reads every .dff and .txd of an IMG archive back through librw (the renderer re3 uses), and checks the .col
// file parses the way CFileLoader::LoadCollisionFile reads it. Prints one line per file; exits 1 on any failure.
// Textures that come out (nearly) all black are listed at the end. Pass - for the .col to check only an IMG, such
// as the models/txd.img cache re3 builds on its first run.
//   rwcheck models/ctw.img models/ctw.dir data/maps/ctw/ctw.col
#include <rw.h>
#include <cstdio>
#include <cstring>
#include <vector>
#include <string>
#include <map>

static std::map<std::string, rw::TexDictionary*> dicts;

using namespace rw;

static std::vector<uint8> readFile(const char *path)
{
	std::vector<uint8> d;
	FILE *f = fopen(path, "rb");
	if(f == nil) return d;
	fseek(f, 0, SEEK_END);
	d.resize(ftell(f));
	fseek(f, 0, SEEK_SET);
	if(fread(d.data(), 1, d.size(), f) != d.size()) d.clear();
	fclose(f);
	return d;
}

static int failures = 0;
static int numTextures = 0;
static std::vector<std::string> darkTextures;

static void checkDff(const char *name, uint8 *data, uint32 size)
{
	StreamMemory s;
	s.open(data, size);
	Clump *c = nil;
	if(findChunk(&s, ID_CLUMP, nil, nil))
		c = Clump::streamRead(&s);
	if(c == nil){ printf("FAIL %s: clump did not load\n", name); failures++; return; }
	int atomics = c->countAtomics();
	FORLIST(lnk, c->atomics){
		Atomic *a = Atomic::fromClump(lnk);
		Geometry *g = a->geometry;
		printf("ok   %s: %d atomic, %d verts, %d tris, %d materials, meshes %d, flags 0x%x",
		       name, atomics, g->numVertices, g->numTriangles, g->matList.numMaterials,
		       g->meshHeader ? g->meshHeader->numMeshes : -1, g->flags);
		for(int i = 0; i < g->matList.numMaterials; i++){
			Material *m = g->matList.materials[i];
			printf(" [%s a%d]", m->texture ? m->texture->name : "-", m->color.alpha);
		}
		Sphere *bs = &g->morphTargets[0].boundingSphere;
		printf(" sphere (%.1f %.1f %.1f r %.1f)\n", bs->center.x, bs->center.y, bs->center.z, bs->radius);
		for(int i = 0; i < g->numTriangles; i++)
			for(int k = 0; k < 3; k++)
				if(g->triangles[i].v[k] >= g->numVertices){ printf("FAIL %s: bad index\n", name); failures++; return; }
	}
	c->destroy();
}

static TexDictionary *checkTxd(const char *name, uint8 *data, uint32 size)
{
	StreamMemory s;
	s.open(data, size);
	TexDictionary *txd = nil;
	if(findChunk(&s, ID_TEXDICTIONARY, nil, nil))
		txd = TexDictionary::streamRead(&s);
	if(txd == nil){ printf("FAIL %s: txd did not load\n", name); failures++; return nil; }
	printf("ok   %s: %d textures", name, txd->count());
	FORLIST(lnk, txd->textures){
		Texture *t = Texture::fromDict(lnk);
		Raster *r = t->raster;
		Image *img = r->toImage();
		uint8 px[4] = {0, 0, 0, 0};
		double mean = -1.0;
		if(img){
			if(img->depth == 32) memcpy(px, img->pixels, 4);
			else if(img->depth == 24) memcpy(px, img->pixels, 3), px[3] = 255;
			if(img->depth == 32 || img->depth == 24){
				// mean brightness of the opaque-enough pixels
				int bpp = img->depth / 8;
				double sum = 0.0;
				long n = 0;
				for(int y = 0; y < img->height; y++)
					for(int x = 0; x < img->width; x++){
						uint8 *p = img->pixels + y * img->stride + x * bpp;
						if(bpp == 4 && p[3] < 16) continue;
						sum += (p[0] + p[1] + p[2]) / 3.0;
						n++;
					}
				mean = n ? sum / n : 0.0;
			}
			img->destroy();
		}
		numTextures++;
		char desc[160];
		snprintf(desc, sizeof(desc), "%s/%s %dx%d fmt 0x%x mean %.1f", name, t->name, r->width, r->height, r->format, mean);
		if(mean >= 0.0 && mean < 12.0)
			darkTextures.push_back(desc);
		printf(" [%s %dx%d fmt 0x%x first pixel %d,%d,%d,%d mean %.1f]", t->name, r->width, r->height, r->format,
		       px[0], px[1], px[2], px[3], mean);
	}
	printf("\n");
	return txd;
}

static void checkCol(const char *path)
{
	std::vector<uint8> d = readFile(path);
	size_t p = 0;
	int models = 0;
	while(p + 8 <= d.size()){
		uint32 ident, size;
		memcpy(&ident, &d[p], 4);
		memcpy(&size, &d[p + 4], 4);
		if(ident != 0x4C4C4F43 || p + 8 + size > d.size()){ printf("FAIL col: bad header at %zu\n", p); failures++; return; }
		uint8 *b = &d[p + 8];
		char model[24];
		memcpy(model, b, 24);
		model[23] = 0;
		uint8 *q = b + 24;
		float r = *(float*)q;
		float *bb = (float*)(q + 16);
		int16 ns = *(int16*)(q + 40); q += 44 + ns * 20;
		int16 nl = *(int16*)q; q += 4 + nl * 24;
		int16 nb = *(int16*)q; q += 4 + nb * 28;
		int32 nv = *(int16*)q; q += 4 + nv * 12;
		int16 nt = *(int16*)q; q += 4;
		for(int i = 0; i < nt; i++){
			int32 *t = (int32*)(q + i * 16);
			if(t[0] >= nv || t[1] >= nv || t[2] >= nv || t[0] < 0){ printf("FAIL col %s: bad triangle\n", model); failures++; return; }
		}
		q += nt * 16;
		if(q != b + size){ printf("FAIL col %s: size mismatch\n", model); failures++; return; }
		printf("ok   col %s: r %.1f box (%.1f %.1f %.1f)-(%.1f %.1f %.1f), %d boxes, %d verts, %d tris\n",
		       model, r, bb[0], bb[1], bb[2], bb[3], bb[4], bb[5], nb, nv, nt);
		p += 8 + size;
		models++;
	}
	printf("%d collision models\n", models);
}

int main(int argc, char **argv)
{
	if(argc < 4){ fprintf(stderr, "usage: rwcheck ctw.img ctw.dir ctw.col\n"); return 2; }
	Engine::init(nil);
	registerMeshPlugin();
	registerNativeDataPlugin();
	Engine::open(nil);
	Engine::start();
	std::vector<uint8> img = readFile(argv[1]), dir = readFile(argv[2]);
	// texture dictionaries first: a model's materials find their textures in the current dictionary, as in re3
	for(int pass = 0; pass < 2; pass++)
	for(size_t i = 0; i + 32 <= dir.size(); i += 32){
		uint32 off, size;
		memcpy(&off, &dir[i], 4);
		memcpy(&size, &dir[i + 4], 4);
		char name[25] = {0};
		memcpy(name, &dir[i + 8], 24);
		uint8 *data = &img[(size_t)off * 2048];
		uint32 bytes = size * 2048;
		const char *ext = strrchr(name, '.');
		if(pass == 1 && ext && strcmp(ext, ".dff") == 0){
			std::string txdName = std::string(name, ext - name);
			TexDictionary::setCurrent(dicts[txdName]);
			checkDff(name, data, bytes);
		}else if(pass == 0 && ext && strcmp(ext, ".txd") == 0)
			dicts[std::string(name, ext - name)] = checkTxd(name, data, bytes);
	}
	if(strcmp(argv[3], "-") != 0)
		checkCol(argv[3]);
	printf("%d of %d textures are nearly all black%s\n", (int)darkTextures.size(), numTextures,
	       darkTextures.empty() ? "" : ":");
	for(size_t i = 0; i < darkTextures.size(); i++)
		printf("  dark %s\n", darkTextures[i].c_str());
	printf(failures ? "%d failures\n" : "all good\n", failures);
	return failures ? 1 : 0;
}
