"""FLUX.2 text-encoder pairing — pytest (`uv run pytest tests/test_flux2_text_encoder.py -q`).

The flux2 family splits three ways by which text encoder the DiT was trained
against, and the wrong one does NOT fail loud — it renders garbage or crashes
KSampler minutes in with a shape-mismatch. dyva must pick the exact encoder from
the host's own enums, and REJECT a host that lacks it rather than drive it wrong.
The contract (node-classifier.json `edit` families + dyva/README image section):

  * FLUX.2 klein 9B  -> Qwen3-8B encoder (qwen_3_8b, 4096-dim). The 4B (2560-dim)
    crashes: 'mat1 and mat2 shapes cannot be multiplied (512x7680 and 12288x4096)'.
  * FLUX.2 klein 4B  -> Qwen3-4B encoder specifically. The 8B size-mismatches:
    'embed_tokens.weight 4096 vs 2560'. Not Mistral, not the 8B.
  * FLUX.2 (non-klein) -> Mistral-3 small. klein is excluded via a (?!.*klein)
    lookahead so a klein+Mistral host is rejected at PLANNING, not after a render.

These are the pairings the user flagged as "definitely needs tests absolutely."
All pure/deterministic — synthetic host enums, no network, no DB.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dyva   # noqa: E402

VAE = ["flux2-vae.safetensors"]


def _fam(name):
    return next(f for f in dyva.load_edit_families() if f["name"] == name)


def _host(unets=(), clips=(), vaes=VAE, clip_types=("flux2",)):
    """A ComfyUI /object_info shape with just the nodes _edit_plan inspects."""
    return {
        "ReferenceLatent": {}, "KSampler": {}, "VAEDecode": {}, "LoadImage": {},
        "SaveImage": {}, "FluxGuidance": {}, "FluxKontextImageScale": {},
        "ImageScaleToTotalPixels": {},
        "UNETLoader": {"input": {"required": {"unet_name": [list(unets)]}}},
        "CLIPLoader": {"input": {"required": {"clip_name": [list(clips)],
                                              "type": [list(clip_types)]}}},
        "VAELoader": {"input": {"required": {"vae_name": [list(vaes)]}}},
    }


# ---- family-table pairing invariants (pattern level) -----------------------
# These bite directly if someone edits a clip/model regex in node-classifier.json,
# independent of the _edit_plan plumbing.

def test_klein9b_encoder_pattern_takes_8b_rejects_4b():
    pats = dyva._hints(_fam("Flux2Klein9B").get("clip"))
    assert dyva._pick(["qwen_3_8b.safetensors"], *pats) == "qwen_3_8b.safetensors"
    # the 4B is the documented crash (7680 vs 12288) — must NOT be accepted for 9B
    assert dyva._pick(["qwen_3_4b.safetensors"], *pats) is None


def test_klein4b_encoder_pattern_takes_4b_rejects_8b():
    pats = dyva._hints(_fam("Flux2Klein").get("clip"))
    assert dyva._pick(["qwen_3_4b.safetensors"], *pats) == "qwen_3_4b.safetensors"
    # the 8B is the documented size-mismatch (4096 vs 2560) — must NOT be accepted
    assert dyva._pick(["qwen_3_8b.safetensors"], *pats) is None
    # ...and never Mistral
    assert dyva._pick(["mistral_3_small_flux2.safetensors"], *pats) is None


def test_flux2_nonklein_encoder_is_mistral():
    pats = dyva._hints(_fam("Flux2").get("clip"))
    assert dyva._pick(["mistral_3_small_flux2.safetensors"], *pats) == "mistral_3_small_flux2.safetensors"
    assert dyva._pick(["qwen_3_8b.safetensors"], *pats) is None


def test_flux2_model_regex_excludes_klein_via_lookahead():
    # the (?!.*klein) is what keeps a klein host from being driven as plain flux2
    # (with Mistral) — the whole point of rejecting klein+Mistral at planning.
    rx = re.compile(_fam("Flux2")["model"])
    assert rx.search("flux2-dev.safetensors")
    assert not rx.search("flux2-klein-9b.safetensors")
    assert not rx.search("flux-2-klein.safetensors")


def test_klein9b_is_listed_before_klein4b_so_9b_matches_first():
    # both families' model regex match a '...klein-9b...' name; order decides. If
    # the bare 'klein' family came first a 9B host would be sent the 4B encoder.
    names = [f["name"] for f in dyva.load_edit_families()]
    assert names.index("Flux2Klein9B") < names.index("Flux2Klein")


# ---- end-to-end _edit_plan: the right host gets the right encoder ----------

def test_plan_klein9b_host_with_8b_picks_9b_family_and_8b_encoder():
    r = dyva._edit_plan(_host(["flux2-klein-9b.safetensors"],
                              ["qwen_3_8b.safetensors", "qwen_3_4b.safetensors"]))
    assert r["family"]["name"] == "Flux2Klein9B"
    assert r["loader"]["clip"] == "qwen_3_8b.safetensors"   # NOT the 4B also present


def test_plan_klein4b_host_picks_4b_family_and_4b_encoder():
    r = dyva._edit_plan(_host(["flux2-klein.safetensors"], ["qwen_3_4b.safetensors"]))
    assert r["family"]["name"] == "Flux2Klein"
    assert r["loader"]["clip"] == "qwen_3_4b.safetensors"


def test_plan_nonklein_flux2_host_picks_mistral():
    r = dyva._edit_plan(_host(["flux2-dev.safetensors"], ["mistral_3_small_flux2.safetensors"]))
    assert r["family"]["name"] == "Flux2"
    assert r["loader"]["clip"] == "mistral_3_small_flux2.safetensors"


# ---- rejection beats a wrong render ----------------------------------------

def test_klein_host_with_only_mistral_is_rejected_not_mispaired():
    # Flux2 would pick Mistral but its lookahead excludes klein; the klein families
    # demand a Qwen3 encoder this host lacks. Net: no plan, with a stated reason —
    # never "drive klein with Mistral and render minutes of garbage".
    reasons = []
    r = dyva._edit_plan(_host(["flux2-klein-9b.safetensors"],
                              ["mistral_3_small.safetensors"]), reasons=reasons)
    assert r is None
    assert any("text encoder" in x for x in reasons)


def test_missing_vae_is_a_stated_reason_not_a_null_vae_render():
    reasons = []
    r = dyva._edit_plan(_host(["flux2-klein-9b.safetensors"], ["qwen_3_8b.safetensors"],
                              vaes=[]), reasons=reasons)
    assert r is None
    assert any("VAE" in x for x in reasons)


def test_host_clip_loader_lacking_flux2_type_is_rejected():
    # the files can be present but a CLIPLoader with no 'flux2' type can't load them
    reasons = []
    r = dyva._edit_plan(_host(["flux2-klein-9b.safetensors"], ["qwen_3_8b.safetensors"],
                              clip_types=["qwen_image"]), reasons=reasons)
    assert r is None
    assert any("flux2" in x for x in reasons)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
