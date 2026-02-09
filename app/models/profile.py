"""
User Profile Models
Structured personal profile that auto-updates from conversations
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from uuid import UUID, uuid4


class UserProfile(BaseModel):
    """Structured user profile - grows automatically from conversations"""
    
    # Identity
    user_id: str
    name: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    language: Optional[str] = "English"
    location: Optional[str] = None
    
    # Professional
    education: Optional[str] = None
    profession: Optional[str] = None
    workplace: Optional[str] = None
    experience_years: Optional[int] = None
    skills: List[str] = Field(default_factory=list)
    
    # Personal
    relationship_status: Optional[str] = None
    partner_name: Optional[str] = None
    family: List[str] = Field(default_factory=list)
    
    # Interests & Personality
    interests: List[str] = Field(default_factory=list)
    hobbies: List[str] = Field(default_factory=list)
    goals: List[str] = Field(default_factory=list)
    personality_traits: List[str] = Field(default_factory=list)
    
    # Communication Style
    writing_style: Optional[str] = None  # "formal", "casual", "technical"
    prefers_short_responses: bool = False
    uses_emojis: bool = False
    tone_preference: Optional[str] = None  # "friendly", "professional", "humorous"
    
    # Preferences
    likes: Dict[str, List[str]] = Field(default_factory=dict)  # {"foods": [...], "music": [...]}
    dislikes: Dict[str, List[str]] = Field(default_factory=dict)
    
    # Temporal Patterns
    timezone: Optional[str] = None
    active_hours: List[str] = Field(default_factory=list)  # ["09:00-12:00", "18:00-22:00"]
    routines: Dict[str, str] = Field(default_factory=dict)  # {"morning": "coffee at 7am"}
    
    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_conversation: Optional[datetime] = None
    total_conversations: int = 0
    profile_completeness: float = 0.0  # 0-100%
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ProfileUpdate(BaseModel):
    """Update request for user profile"""
    field_name: str
    value: Any
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    source_memory_id: Optional[UUID] = None


class ProfileSummary(BaseModel):
    """Compact profile summary for LLM context"""
    user_id: str
    name: Optional[str]
    key_facts: List[str] = Field(default_factory=list)
    preferences: List[str] = Field(default_factory=list)
    communication_style: str = "casual"
    profile_completeness: float
    
    @classmethod
    def from_profile(cls, profile: UserProfile) -> "ProfileSummary":
        """Generate summary from full profile"""
        key_facts = []
        
        # Identity
        if profile.name:
            key_facts.append(f"Name: {profile.name}")
        if profile.age:
            key_facts.append(f"Age: {profile.age}")
        if profile.location:
            key_facts.append(f"Location: {profile.location}")
        
        # Professional
        if profile.profession:
            key_facts.append(f"Profession: {profile.profession}")
        if profile.workplace:
            key_facts.append(f"Workplace: {profile.workplace}")
        if profile.skills:
            key_facts.append(f"Skills: {', '.join(profile.skills[:5])}")
        
        # Personal
        if profile.partner_name:
            key_facts.append(f"Partner: {profile.partner_name}")
        
        # Preferences
        preferences = []
        if profile.likes:
            for category, items in profile.likes.items():
                if items:
                    preferences.append(f"Likes {category}: {', '.join(items[:3])}")
        if profile.dislikes:
            for category, items in profile.dislikes.items():
                if items:
                    preferences.append(f"Dislikes {category}: {', '.join(items[:3])}")
        
        # Communication style
        style = "casual"
        if profile.writing_style:
            style = profile.writing_style
        if profile.prefers_short_responses:
            style += ", prefers brief answers"
        if profile.uses_emojis:
            style += ", uses emojis"
        
        return cls(
            user_id=profile.user_id,
            name=profile.name,
            key_facts=key_facts[:10],  # Top 10 facts
            preferences=preferences[:5],  # Top 5 preferences
            communication_style=style,
            profile_completeness=profile.profile_completeness
        )
