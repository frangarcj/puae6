// Executes the real upstream seed and xorshift bodies. The reference receives
// an already-normalized seed, while P6 must normalize zero itself.
#include "sysdeps.h"
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>

static struct { uae_u32 seed; } currprefs{};
uae_u32 uaesetrandseed(uae_u32);
#ifdef TEST_REFERENCE
uae_u32 getlocaltime(void);
uae_u32 getlocaltime(void) { std::abort(); }
#endif

#include "main_seed_under_test.h"

static uae_u32 hash_word(uae_u32 h, uae_u32 v)
{
    for (int i=0;i<4;i++) h=(h^((v>>(8*i))&255u))*16777619u;
    return h;
}

int main()
{
    const uae_u32 inputs[]={0,1,2,0x80000000u,0xffffffffu,0x12345678u};
    const uae_u32 sequences[][5]={
        {0x00042021u,0x04080601u,0x9dcca8c5u,0x1255994fu,0x8ef917d1u},
        {0x00042021u,0x04080601u,0x9dcca8c5u,0x1255994fu,0x8ef917d1u},
        {0x00084042u,0x08008c02u,0x2998d882u,0x6398b906u,0xe3b0281au},
        {0x80084000u,0x89088484u,0xaa914148u,0xd5b80294u,0x68b7e441u},
        {0x0003e01fu,0xfc07fdffu,0x74bb9843u,0xf1cc88dau,0x7a28ee91u},
        {0x87985aa5u,0x155b24a3u,0x4820f4c4u,0x81b3ac98u,0x703a0788u}
    };
    uae_u32 hash=2166136261u;
    for (unsigned c=0;c<sizeof(inputs)/sizeof(inputs[0]);c++) {
        const uae_u32 expected_seed=inputs[c] ? inputs[c] : 1;
#ifdef TEST_REFERENCE
        currprefs.seed=expected_seed;
#else
        currprefs.seed=inputs[c];
#endif
        randseed=xorshiftstate=0;
        uaerandomizeseed();
        assert(randseed==expected_seed && xorshiftstate==expected_seed);
        assert(uaerandgetseed()==expected_seed && xorshiftstate==expected_seed);
        for (int i=0;i<5;i++) {
            const uae_u32 v=uaerand();
            assert(v==sequences[c][i]);
            hash=hash_word(hash,v);
        }
        const uae_u32 saved_seed=randseed, saved_state=xorshiftstate;
        assert(saved_state!=saved_seed);
        assert(uaerandgetseed()==saved_seed && xorshiftstate==saved_state);
        const uae_u32 next=uaerand();
        randseed=0xa5a5a5a5u; xorshiftstate=0x5a5a5a5au;
        randseed=saved_seed; xorshiftstate=saved_state;
        assert(uaerand()==next);
        hash=hash_word(hash,next);

        uaesetrandseed(expected_seed);
        xorshiftstate=0; // upstream lazy reinitialization starts from randseed
        assert(uaerand()==sequences[c][0]);
        hash=hash_word(hash,randseed);
    }
    std::printf("%08x\n",hash);
}
