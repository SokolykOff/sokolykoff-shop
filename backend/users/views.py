import json
from urllib.parse import parse_qs
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from .models import Profile


def parse_body(request):
    """Универсальный парсер: понимает JSON, form-data и query-параметры без ошибок 500"""
    data = {}
    if request.GET:
        data.update(request.GET.dict())
    if request.body:
        raw = request.body.decode("utf-8", errors="ignore").strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, str):
                    parsed = json.loads(parsed)
                if isinstance(parsed, dict):
                    data.update(parsed)
                elif isinstance(parsed, list):
                    return parsed
            except Exception:
                # Если пришли form-urlencoded данные или JSON внутри ключа формы
                for k, v in parse_qs(raw).items():
                    if k.startswith("{"):
                        try:
                            data.update(json.loads(k))
                        except Exception:
                            pass
                    else:
                        data[k] = v[0] if v else ""
    if not data and request.POST:
        for k, v in request.POST.items():
            if k.startswith("{"):
                try:
                    data.update(json.loads(k))
                except Exception:
                    pass
            else:
                data[k] = v
    return data


def serialize_image(file_field, default_alt="image"):
    if file_field and hasattr(file_field, "url"):
        try:
            return {"src": file_field.url, "alt": default_alt}
        except Exception:
            pass
    return {"src": "/static/shop/placeholder-product.svg", "alt": default_alt}


@require_POST
def sign_in_view(request):
    data = parse_body(request)
    username = data.get("username") or data.get("login") or ""
    password = data.get("password") or ""
    user = authenticate(request, username=username, password=password)
    if user:
        login(request, user)
        Profile.objects.get_or_create(user=user, defaults={"full_name": user.first_name or user.username})
        return JsonResponse({"status": "ok"})
    return JsonResponse({"error": "Неверный логин или пароль"}, status=400)


@require_POST
def sign_up_view(request):
    data = parse_body(request)
    username = (data.get("username") or data.get("login") or "").strip()
    password = (data.get("password") or "").strip()
    name = (data.get("name") or username).strip()

    if not username or not password:
        return JsonResponse({"error": "Логин и пароль обязательны"}, status=400)
    if User.objects.filter(username=username).exists():
        return JsonResponse({"error": "Пользователь уже существует"}, status=400)

    try:
        validate_password(password, User(username=username, first_name=name))
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)
    user = User.objects.create_user(username=username, password=password, first_name=name)
    Profile.objects.get_or_create(user=user, defaults={"full_name": name})
    login(request, user)
    return JsonResponse({"status": "ok"})


@require_POST
def sign_out_view(request):
    logout(request)
    return JsonResponse({"status": "ok"})


def profile_view(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Unauthorized"}, status=401)
    profile, _ = Profile.objects.get_or_create(user=request.user, defaults={"full_name": request.user.username})
    if request.method == "POST":
        data = parse_body(request)
        profile.full_name = data.get("fullName", profile.full_name)
        profile.phone = data.get("phone") or None
        profile.email = data.get("email") or None
        profile.save()
    return JsonResponse({
        "fullName": profile.full_name or request.user.username,
        "email": profile.email or request.user.email or "",
        "phone": profile.phone or "",
        "avatar": serialize_image(profile.avatar, profile.full_name) if profile.avatar else {"src": "", "alt": ""},
    })


def profile_avatar_view(request):
    if request.method == "POST" and request.user.is_authenticated:
        profile, _ = Profile.objects.get_or_create(user=request.user)
        if "avatar" in request.FILES:
            profile.avatar = request.FILES["avatar"]
            profile.save()
        return JsonResponse(serialize_image(profile.avatar, profile.full_name))
    return JsonResponse({"error": "Bad request"}, status=400)


def profile_password_view(request):
    if request.method == "POST" and request.user.is_authenticated:
        data = parse_body(request) if isinstance(parse_body(request), dict) else {}
        curr_pass = data.get("passwordCurrent") or data.get("currentPassword") or ""
        new_pass = data.get("password") or data.get("newPassword") or ""
        if not curr_pass or not request.user.check_password(curr_pass):
            return JsonResponse({"error": "Неверный текущий пароль"}, status=400)
        try:
            validate_password(new_pass, request.user)
        except ValidationError as exc:
            return JsonResponse({"error": " ".join(exc.messages)}, status=400)
        request.user.set_password(new_pass)
        request.user.save()
        login(request, request.user)
        return JsonResponse({"status": "ok"})
    return JsonResponse({"error": "Bad request"}, status=400)

def delete_account_view(request):
    if request.method == "POST" and request.user.is_authenticated:
        user = request.user
        profile = getattr(user, "profile", None)
        if profile:
            profile.delete()
        logout(request)
        user.is_active = False
        user.save(update_fields=["is_active"])
        return JsonResponse({"status": "deleted"})
    return JsonResponse({"error": "Unauthorized"}, status=401)
