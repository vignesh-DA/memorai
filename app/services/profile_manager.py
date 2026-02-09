"""
User Profile Manager
Auto-updates user profile from conversation memories
"""

import json
from datetime import datetime
from typing import Optional, Dict, Any, List
from app.models.profile import UserProfile, ProfileSummary, ProfileUpdate
from app.core.database import get_db_pool
import asyncpg


class ProfileManager:
    """Manages user profiles with auto-update from memories"""
    
    def __init__(self):
        self.pool = None
    
    async def initialize(self):
        """Initialize database pool"""
        self.pool = await get_db_pool()
    
    async def create_profile_table(self):
        """Create user_profiles table if not exists"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id VARCHAR(255) PRIMARY KEY,
                    
                    -- Identity
                    name VARCHAR(255),
                    age INTEGER,
                    gender VARCHAR(50),
                    language VARCHAR(50) DEFAULT 'English',
                    location VARCHAR(255),
                    
                    -- Professional
                    education TEXT,
                    profession VARCHAR(255),
                    workplace VARCHAR(255),
                    experience_years INTEGER,
                    skills JSONB DEFAULT '[]'::jsonb,
                    
                    -- Personal
                    relationship_status VARCHAR(100),
                    partner_name VARCHAR(255),
                    family JSONB DEFAULT '[]'::jsonb,
                    
                    -- Interests
                    interests JSONB DEFAULT '[]'::jsonb,
                    hobbies JSONB DEFAULT '[]'::jsonb,
                    goals JSONB DEFAULT '[]'::jsonb,
                    personality_traits JSONB DEFAULT '[]'::jsonb,
                    
                    -- Communication Style
                    writing_style VARCHAR(50),
                    prefers_short_responses BOOLEAN DEFAULT FALSE,
                    uses_emojis BOOLEAN DEFAULT FALSE,
                    tone_preference VARCHAR(50),
                    
                    -- Preferences
                    likes JSONB DEFAULT '{}'::jsonb,
                    dislikes JSONB DEFAULT '{}'::jsonb,
                    
                    -- Temporal
                    timezone VARCHAR(50),
                    active_hours JSONB DEFAULT '[]'::jsonb,
                    routines JSONB DEFAULT '{}'::jsonb,
                    
                    -- Metadata
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    last_conversation TIMESTAMP,
                    total_conversations INTEGER DEFAULT 0,
                    profile_completeness FLOAT DEFAULT 0.0
                );
                
                CREATE INDEX IF NOT EXISTS idx_profiles_user_id ON user_profiles(user_id);
            """)
    
    async def get_or_create_profile(self, user_id: str) -> UserProfile:
        """Get existing profile or create new one"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM user_profiles WHERE user_id = $1",
                user_id
            )
            
            if row:
                return self._row_to_profile(row)
            
            # Create new profile
            await conn.execute(
                """
                INSERT INTO user_profiles (user_id)
                VALUES ($1)
                """,
                user_id
            )
            
            return UserProfile(user_id=user_id)
    
    async def update_profile_from_memory(
        self,
        user_id: str,
        memory_content: str,
        memory_type: str,
        entities: List[Dict[str, Any]],
        context: Dict[str, Any]
    ) -> bool:
        """Auto-update profile based on extracted memory"""
        profile = await self.get_or_create_profile(user_id)
        updated = False
        
        # Extract profile updates based on memory type
        updates = {}
        
        # ENTITY type memories
        if memory_type == "ENTITY":
            for entity in entities:
                entity_type = entity.get("type", "").lower()
                entity_text = entity.get("text", "")
                
                # Name extraction
                if "name" in memory_content.lower() and not profile.name:
                    # Check if it's the user's name (not someone else's)
                    if any(word in memory_content.lower() for word in ["my name", "i am", "i'm", "call me"]):
                        updates["name"] = entity_text
                        updated = True
                
                # Partner/fiancé
                if any(word in memory_content.lower() for word in ["fiancé", "fiancee", "partner", "boyfriend", "girlfriend", "spouse", "wife", "husband"]):
                    updates["partner_name"] = entity_text
                    updates["relationship_status"] = self._extract_relationship_status(memory_content)
                    updated = True
                
                # Location
                if any(word in memory_content.lower() for word in ["based in", "live in", "from", "located in"]):
                    updates["location"] = entity_text
                    updated = True
                
                # Workplace
                if any(word in memory_content.lower() for word in ["work at", "works at", "company", "employer"]):
                    updates["workplace"] = entity_text
                    updated = True
        
        # FACT type memories
        elif memory_type == "FACT":
            # Age
            import re
            age_match = re.search(r'(\d+)\s*years?\s*old', memory_content.lower())
            if age_match:
                updates["age"] = int(age_match.group(1))
                updated = True
            
            # Profession
            if any(word in memory_content.lower() for word in ["engineer", "developer", "scientist", "analyst", "manager", "consultant"]):
                for word in ["engineer", "developer", "scientist", "analyst", "manager", "consultant", "designer"]:
                    if word in memory_content.lower():
                        # Extract full profession title
                        profession = self._extract_profession(memory_content)
                        if profession:
                            updates["profession"] = profession
                            updated = True
                        break
            
            # Skills
            if any(word in memory_content.lower() for word in ["expert in", "skilled in", "specializes in", "experience in"]):
                skills = self._extract_skills(memory_content)
                if skills:
                    current_skills = profile.skills or []
                    updates["skills"] = list(set(current_skills + skills))
                    updated = True
            
            # Experience years
            exp_match = re.search(r'(\d+)\s*years?\s*of\s*experience', memory_content.lower())
            if exp_match:
                updates["experience_years"] = int(exp_match.group(1))
                updated = True
        
        # PREFERENCE type memories
        elif memory_type == "PREFERENCE":
            likes = profile.likes or {}
            
            # Food preferences
            if any(word in memory_content.lower() for word in ["loves", "likes", "favorite", "enjoys"]):
                if "food" in memory_content.lower() or any(food in memory_content.lower() for food in ["pizza", "pasta", "dosa", "idli", "biryani"]):
                    food_items = self._extract_preference_items(memory_content)
                    if food_items:
                        likes["foods"] = likes.get("foods", []) + food_items
                        updates["likes"] = likes
                        updated = True
            
            # Music preferences
            if "music" in memory_content.lower() or any(genre in memory_content.lower() for genre in ["rock", "pop", "jazz", "classical"]):
                music_items = self._extract_preference_items(memory_content)
                if music_items:
                    likes["music"] = likes.get("music", []) + music_items
                    updates["likes"] = likes
                    updated = True
        
        # Apply updates
        if updated:
            await self._apply_updates(user_id, updates)
            await self._update_completeness(user_id)
        
        return updated
    
    async def _apply_updates(self, user_id: str, updates: Dict[str, Any]):
        """Apply field updates to profile"""
        if not updates:
            return
        
        # Build dynamic UPDATE query
        set_clauses = []
        values = []
        param_num = 1
        
        for field, value in updates.items():
            if isinstance(value, (list, dict)):
                set_clauses.append(f"{field} = ${param_num}::jsonb")
                values.append(json.dumps(value))
            else:
                set_clauses.append(f"{field} = ${param_num}")
                values.append(value)
            param_num += 1
        
        # Add updated_at
        set_clauses.append(f"updated_at = ${param_num}")
        values.append(datetime.utcnow())
        param_num += 1
        
        # Add user_id for WHERE clause
        values.append(user_id)
        
        query = f"""
            UPDATE user_profiles
            SET {', '.join(set_clauses)}
            WHERE user_id = ${param_num}
        """
        
        async with self.pool.acquire() as conn:
            await conn.execute(query, *values)
    
    async def _update_completeness(self, user_id: str):
        """Calculate and update profile completeness score"""
        profile = await self.get_or_create_profile(user_id)
        
        # Count filled fields
        total_fields = 0
        filled_fields = 0
        
        # Core fields (higher weight)
        core_fields = ["name", "age", "location", "profession"]
        for field in core_fields:
            total_fields += 2  # Weight = 2
            value = getattr(profile, field, None)
            if value:
                filled_fields += 2
        
        # Secondary fields
        secondary_fields = [
            "gender", "education", "workplace", "experience_years",
            "relationship_status", "partner_name", "writing_style"
        ]
        for field in secondary_fields:
            total_fields += 1
            value = getattr(profile, field, None)
            if value:
                filled_fields += 1
        
        # List fields
        list_fields = ["skills", "interests", "hobbies", "goals"]
        for field in list_fields:
            total_fields += 1
            value = getattr(profile, field, [])
            if value and len(value) > 0:
                filled_fields += 1
        
        completeness = (filled_fields / total_fields) * 100 if total_fields > 0 else 0
        
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE user_profiles SET profile_completeness = $1 WHERE user_id = $2",
                completeness,
                user_id
            )
    
    async def get_profile_summary(self, user_id: str) -> ProfileSummary:
        """Get compact profile summary for LLM context"""
        profile = await self.get_or_create_profile(user_id)
        return ProfileSummary.from_profile(profile)
    
    def _row_to_profile(self, row: asyncpg.Record) -> UserProfile:
        """Convert database row to UserProfile object"""
        return UserProfile(
            user_id=row["user_id"],
            name=row["name"],
            age=row["age"],
            gender=row["gender"],
            language=row["language"],
            location=row["location"],
            education=row["education"],
            profession=row["profession"],
            workplace=row["workplace"],
            experience_years=row["experience_years"],
            skills=row["skills"] or [],
            relationship_status=row["relationship_status"],
            partner_name=row["partner_name"],
            family=row["family"] or [],
            interests=row["interests"] or [],
            hobbies=row["hobbies"] or [],
            goals=row["goals"] or [],
            personality_traits=row["personality_traits"] or [],
            writing_style=row["writing_style"],
            prefers_short_responses=row["prefers_short_responses"],
            uses_emojis=row["uses_emojis"],
            tone_preference=row["tone_preference"],
            likes=row["likes"] or {},
            dislikes=row["dislikes"] or {},
            timezone=row["timezone"],
            active_hours=row["active_hours"] or [],
            routines=row["routines"] or {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_conversation=row["last_conversation"],
            total_conversations=row["total_conversations"],
            profile_completeness=row["profile_completeness"]
        )
    
    # Helper methods for extraction
    def _extract_relationship_status(self, text: str) -> str:
        if "fiancé" in text.lower() or "fiancee" in text.lower():
            return "engaged"
        elif "married" in text.lower() or "wife" in text.lower() or "husband" in text.lower():
            return "married"
        elif "boyfriend" in text.lower() or "girlfriend" in text.lower():
            return "in_relationship"
        return "unknown"
    
    def _extract_profession(self, text: str) -> Optional[str]:
        """Extract full profession title"""
        import re
        patterns = [
            r'(AI|ML|Data|Software|Full Stack|Backend|Frontend|DevOps)?\s*(Engineer|Developer|Scientist|Analyst)',
            r'(Senior|Junior|Lead)?\s*(Engineer|Developer|Manager)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(0).strip()
        return None
    
    def _extract_skills(self, text: str) -> List[str]:
        """Extract technical skills"""
        skill_keywords = [
            "Python", "JavaScript", "TypeScript", "Java", "C++", "Go", "Rust",
            "React", "Vue", "Angular", "Node.js", "Django", "Flask", "FastAPI",
            "Machine Learning", "ML", "Deep Learning", "NLP", "Computer Vision",
            "AI", "Data Science", "SQL", "PostgreSQL", "MongoDB", "Redis",
            "Docker", "Kubernetes", "AWS", "Azure", "GCP", "Git"
        ]
        
        found_skills = []
        text_lower = text.lower()
        for skill in skill_keywords:
            if skill.lower() in text_lower:
                found_skills.append(skill)
        
        return found_skills
    
    def _extract_preference_items(self, text: str) -> List[str]:
        """Extract specific items from preference text"""
        # Simple extraction - could be enhanced with NLP
        import re
        # Find items after keywords like "loves", "likes", "favorite"
        pattern = r'(loves?|likes?|favorites?|enjoys?)\s+([^,.!?]+)'
        matches = re.findall(pattern, text, re.IGNORECASE)
        items = []
        for match in matches:
            item = match[1].strip()
            if item and len(item) < 100:  # Reasonable length
                items.append(item)
        return items


# Global instance
profile_manager = ProfileManager()
