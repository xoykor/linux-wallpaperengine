#pragma once

#include "WallpaperEngine/Data/Model/Object.h"
#include "WallpaperEngine/Maths.h"

#include <cmath>
#include <glm/geometric.hpp>
#include <glm/gtc/constants.hpp>
#include <glm/vec3.hpp>
#include <random>

namespace WallpaperEngine::Render::Objects::ParticleEmitterAlgorithms {

inline uint32_t accumulateRateEmission (float dt, float rate, bool limitOnePerFrame, float& emissionTimer) {
    emissionTimer += dt * rate;
    uint32_t toEmit = static_cast<uint32_t> (emissionTimer);
    emissionTimer -= static_cast<float> (toEmit);
    if (limitOnePerFrame && toEmit > 1) {
	toEmit = 1;
    }
    return toEmit;
}

inline glm::vec3
sampleBoxOffset (const Data::Model::ParticleEmitter& emitter, const glm::vec3& directions, std::mt19937& rng) {
    glm::vec3 offset;
    for (int axis = 0; axis < 3; axis++) {
	float distance = Maths::randomFloat (rng, emitter.distanceMin[axis], emitter.distanceMax[axis]);
	if (Maths::randomFloat (rng, 0.0f, 1.0f) < 0.5f) {
	    distance = -distance;
	}
	offset[axis] = distance;
    }
    return offset * directions;
}

inline glm::vec3
sampleSphereOffset (const Data::Model::ParticleEmitter& emitter, uint32_t particleFlags, std::mt19937& rng) {
    glm::vec3 offset;

    if ((particleFlags & 4) == 0) {
	const float angle = Maths::randomFloat (rng, 0.0f, glm::two_pi<float> ());
	const float minRadius = emitter.distanceMin.x;
	const float maxRadius = emitter.distanceMax.x;
	const float minRadiusSq = minRadius * minRadius;
	const float maxRadiusSq = maxRadius * maxRadius;
	const float radiusXY = std::sqrt (Maths::randomFloat (rng, minRadiusSq, maxRadiusSq));

	offset = glm::vec3 (
	    radiusXY * std::cos (angle), radiusXY * std::sin (angle), Maths::randomFloat (rng, -maxRadius, maxRadius)
	);
	offset *= emitter.directions;
    } else {
	const float theta = Maths::randomFloat (rng, 0.0f, glm::two_pi<float> ());
	const float cosTheta = Maths::randomFloat (rng, -1.0f, 1.0f);
	const float sinTheta = std::sqrt (1.0f - cosTheta * cosTheta);
	offset = glm::vec3 (sinTheta * std::cos (theta), sinTheta * std::sin (theta), cosTheta);

	const float minRadius = emitter.distanceMin.x;
	const float maxRadius = emitter.distanceMax.x;
	const float minRadiusCubed = minRadius * minRadius * minRadius;
	const float maxRadiusCubed = maxRadius * maxRadius * maxRadius;
	const float radius = std::cbrt (Maths::randomFloat (rng, minRadiusCubed, maxRadiusCubed));
	offset *= radius;
	offset *= emitter.directions;
    }

    for (int axis = 0; axis < 3; axis++) {
	if (emitter.sign[axis] == 1) {
	    offset[axis] = std::abs (offset[axis]);
	} else if (emitter.sign[axis] == -1) {
	    offset[axis] = -std::abs (offset[axis]);
	}
    }

    return offset;
}

inline glm::vec3
resolveVelocity (const Data::Model::ParticleEmitter& emitter, const glm::vec3& emitterOffset, std::mt19937& rng) {
    if (emitter.speedMax > 0.0f || emitter.speedMin != 0.0f) {
	const glm::vec3 direction
	    = glm::length (emitterOffset) > 0.0f ? glm::normalize (emitterOffset) : glm::vec3 (0.0f, 1.0f, 0.0f);
	const float speed = Maths::randomFloat (rng, emitter.speedMin, emitter.speedMax);
	return direction * speed;
    }

    return glm::vec3 (0.0f);
}

} // namespace WallpaperEngine::Render::Objects::ParticleEmitterAlgorithms
