from collection.common.registry import register_pipeline
from collection.musinsa.pipeline import MusinsaPipeline
from collection.kream.pipeline import KreamPipeline
from collection.musinsa_used.pipeline import MusinsaUsedPipeline
from collection.zigzag.pipeline import ZigzagPipeline


def register_all_pipelines() -> None:
    register_pipeline(
        "MUSINSA",
        MusinsaPipeline,
    )
    
    register_pipeline(
        "KREAM",
        KreamPipeline,
    )

    register_pipeline(
        "MUSINSA_USED",
        MusinsaUsedPipeline,
    )

    register_pipeline(
        "ZIGZAG",
        ZigzagPipeline,
    )
