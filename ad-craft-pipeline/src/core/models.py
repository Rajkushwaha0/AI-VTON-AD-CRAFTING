import os
import uuid
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any


class PipelineContext:
    """Manages the state and local storage for a single pipeline run."""
    
    def __init__(self, base_dir: str = "runs", run_id: Optional[str] = None, aspect_ratio: str = "9:16"):
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.base_dir = base_dir
        self.aspect_ratio = aspect_ratio
        self.run_dir = os.path.join(base_dir, self.run_id)
        
        self.input_dir = os.path.join(self.run_dir, "input")
        self.primary_input_dir = os.path.join(self.input_dir, "primary")
        self.secondary_input_dir = os.path.join(self.input_dir, "secondary")
        self.images_dir = os.path.join(self.run_dir, "images")
        self.videos_dir = os.path.join(self.run_dir, "videos")
        self.log_file = os.path.join(self.run_dir, "run.log")
        
        self._initialize_directories()
        
    def _initialize_directories(self):
        os.makedirs(self.input_dir, exist_ok=True)
        os.makedirs(self.primary_input_dir, exist_ok=True)
        os.makedirs(self.secondary_input_dir, exist_ok=True)
        os.makedirs(self.images_dir, exist_ok=True)
        os.makedirs(self.videos_dir, exist_ok=True)
        
    def log(self, message: str):
        timestamp = datetime.now().isoformat()
        log_line = f"[{timestamp}] {message}\n"
        print(log_line.strip())
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(log_line)
            
    def save_final_output(self, data: dict):
        output_path = os.path.join(self.run_dir, "final_output.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)


@dataclass
class ProductData:
    title: str = "Product"
    price: Optional[float] = None
    currency: str = "INR"
    key_selling_points: List[str] = field(default_factory=list)
    image_paths: List[str] = field(default_factory=list)
    secondary_title: Optional[str] = None
    secondary_price: Optional[float] = None
    secondary_image_paths: List[str] = field(default_factory=list)
    raw_data: Dict[str, Any] = field(default_factory=dict)
    aspect_ratio: str = "9:16"
    auto_accessories: List[str] = field(default_factory=list)


@dataclass
class StyleAnalysisResult:
    is_cloth: bool = True
    styles: List[str] = field(default_factory=list)
    garment_specs: Dict[str, Any] = field(default_factory=dict)
    pairing_type: str = "single"  # single, pair, co_ord
    suggested_accessories: List[str] = field(default_factory=list)


@dataclass
class GeneratedAsset:
    style_name: str
    image_paths: List[str] = field(default_factory=list)  # [hero, detail, back]
    video_path: Optional[str] = None
    error: Optional[str] = None
    aspect_ratio: str = "9:16"
    qc_report: Optional[Dict[str, Any]] = None
    face_qa_report: Optional[Dict[str, Any]] = None
    scene_qa_report: Optional[Dict[str, Any]] = None
    hero_inspector_report: Optional[Dict[str, Any]] = None
    self_healing_attempts: int = 0
    accessories_styled: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
