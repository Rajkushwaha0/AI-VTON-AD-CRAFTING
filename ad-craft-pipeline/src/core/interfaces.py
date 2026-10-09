from abc import ABC, abstractmethod
from typing import List, Optional
from .models import PipelineContext, ProductData, StyleAnalysisResult, GeneratedAsset


class IIngestionService(ABC):
    """Interface for extracting product data from URLs or raw input."""
    
    @abstractmethod
    def ingest(
        self, 
        context: PipelineContext, 
        product_url: str = None, 
        raw_details: dict = None,
        secondary_url: str = None,
        secondary_details: dict = None
    ) -> ProductData:
        pass


class IStyleAnalyzer(ABC):
    """Interface for analyzing product images and extracting matching styles."""
    
    @abstractmethod
    def analyze(self, context: PipelineContext, product_data: ProductData) -> StyleAnalysisResult:
        pass


class IImageGenerator(ABC):
    """Interface for generating VTON style images in parallel."""
    
    @abstractmethod
    def generate_style_images(
        self, 
        context: PipelineContext, 
        product_data: ProductData, 
        styles: List[str], 
        max_parallel: int = 4,
        aspect_ratio: str = "9:16",
        qc_version: str = "v1"
    ) -> List[GeneratedAsset]:
        pass


class IVideoGenerator(ABC):
    """Interface for generating videos from style images in parallel."""
    
    @abstractmethod
    def generate_videos(
        self, 
        context: PipelineContext, 
        assets: List[GeneratedAsset], 
        max_parallel: int = 4,
        aspect_ratio: str = "9:16",
        duration: int = 10,
        merge_video: bool = True,
        merge_video_idea: str = "smooth",
        user_prompt: Optional[str] = None
    ) -> List[GeneratedAsset]:
        pass
