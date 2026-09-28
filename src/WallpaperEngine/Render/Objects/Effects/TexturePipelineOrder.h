#pragma once

#include <utility>

namespace WallpaperEngine::Render::Objects::Effects::TexturePipelineOrder {

template <
    typename VertexDefaults, typename FragmentDefaults, typename PassTextures, typename PassUserTextures,
    typename OverrideTextures, typename OverrideUserTextures, typename Binds>
void apply (
    VertexDefaults&& vertexDefaults, FragmentDefaults&& fragmentDefaults, PassTextures&& passTextures,
    PassUserTextures&& passUserTextures, OverrideTextures&& overrideTextures, OverrideUserTextures&& overrideUserTextures,
    Binds&& binds
) {
    std::forward<VertexDefaults> (vertexDefaults) ();
    std::forward<FragmentDefaults> (fragmentDefaults) ();
    std::forward<PassTextures> (passTextures) ();
    std::forward<PassUserTextures> (passUserTextures) ();
    std::forward<OverrideTextures> (overrideTextures) ();
    std::forward<OverrideUserTextures> (overrideUserTextures) ();
    std::forward<Binds> (binds) ();
}

} // namespace WallpaperEngine::Render::Objects::Effects::TexturePipelineOrder
