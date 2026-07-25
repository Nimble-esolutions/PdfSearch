from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.conf import settings
from .models import PDFFile, CustomUser, Folder

# ---------------- Upload Form ----------------
class UploadForm(forms.ModelForm):
    """
    Form to upload PDFs. Folder is assigned automatically in the view.
    """
    class Meta:
        model = PDFFile
        fields = ['title', 'file']  # only title and file
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'PDF Title'}),
            'file': forms.FileInput(attrs={'class': 'form-control'}),
        }

    def clean_file(self):
        uploaded = self.cleaned_data['file']
        max_size = int(getattr(settings, 'MAX_FILE_SIZE', 10 * 1024 * 1024))
        if uploaded.size > max_size:
            max_size_mb = getattr(settings, 'MAX_FILE_SIZE_MB', max_size / (1024 * 1024))
            raise forms.ValidationError(
                f'Files must be no larger than {max_size_mb:g} MB.'
            )
        if not uploaded.name.lower().endswith('.pdf'):
            raise forms.ValidationError('Only PDF files are accepted.')
        if uploaded.content_type not in ('application/pdf', 'application/octet-stream'):
            raise forms.ValidationError('The uploaded file must be a PDF.')
        if uploaded.multiple_chunks():
            uploaded.seek(0)
        if uploaded.read(5) != b'%PDF-':
            raise forms.ValidationError('The uploaded file is not a valid PDF.')
        uploaded.seek(0)
        return uploaded

# ---------------- Folder Form ----------------
from django import forms
from .models import Folder

class FolderForm(forms.ModelForm):
    # ✅ New field to enter keywords (comma-separated)
    keywords_input = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter keywords (comma-separated)'
        }),
        label="Keywords"
    )

    class Meta:
        model = Folder
        fields = ['name', 'keywords_input']  # include keywords_input
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter folder name'}),
        }

    # Clean keywords into a list
    def clean_keywords_input(self):
        data = self.cleaned_data.get('keywords_input', '')
        return [kw.strip() for kw in data.split(',') if kw.strip()]

    # Override save to store keywords in model
    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.keywords = self.cleaned_data.get('keywords_input', [])
        if commit:
            instance.save()
        return instance


# ---------------- User Register Form ----------------
DEPARTMENT_CHOICES = [
    ('finance', 'Finance'),
    ('admin', 'Admin'),
    ('operations', 'Operations'),
    ('hr', 'HR'),
    # Add more departments as needed
]

ROLE_CHOICES = [
    ('user', 'User'),
    ('admin', 'Admin'),
    ('superadmin', 'Superadmin'),
]

class UserRegisterForm(UserCreationForm):
    department = forms.ChoiceField(
        choices=DEPARTMENT_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    class Meta:
        model = CustomUser
        fields = ['username', 'email', 'password1', 'password2', 'department', 'role']
        widgets = {
            'username': forms.TextInput(attrs={
                'class': 'form-control', 'placeholder': 'Enter username', 'autofocus': True
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-control', 'placeholder': 'Enter email'
            }),
            'password1': forms.PasswordInput(attrs={
                'class': 'form-control', 'placeholder': 'Enter password', 'id': 'id_password1'
            }),
            'password2': forms.PasswordInput(attrs={
                'class': 'form-control', 'placeholder': 'Confirm password', 'id': 'id_password2'
            }),
        }

    def __init__(self, *args, allow_privileged_roles=False, allow_superadmin=False, **kwargs):
        self.allow_privileged_roles = allow_privileged_roles
        self.allow_superadmin = allow_superadmin
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            css_class = 'form-select' if name in {'department', 'role'} else 'form-control'
            existing = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = f'{existing} {css_class}'.strip()
        if allow_privileged_roles:
            choices = ROLE_CHOICES if allow_superadmin else ROLE_CHOICES[:2]
            self.fields['role'].choices = choices
        else:
            # Public registration must never be able to select an operational role.
            self.fields.pop('role', None)

    def save(self, commit=True):
        user = super().save(commit=False)
        if not self.allow_privileged_roles:
            user.role = 'user'
        if commit:
            user.save()
        return user


class UserManageForm(forms.ModelForm):
    """Edit operator-managed identity fields without exposing permissions broadly."""

    department = forms.ChoiceField(choices=DEPARTMENT_CHOICES)
    role = forms.ChoiceField(choices=ROLE_CHOICES)

    class Meta:
        model = CustomUser
        fields = ['email', 'department', 'role']

    def __init__(self, *args, allow_superadmin=False, lock_role=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['role'].choices = ROLE_CHOICES if allow_superadmin else ROLE_CHOICES[:2]
        self.fields['role'].disabled = lock_role
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
        self.fields['department'].widget.attrs['class'] = 'form-select'
        self.fields['role'].widget.attrs['class'] = 'form-select'
